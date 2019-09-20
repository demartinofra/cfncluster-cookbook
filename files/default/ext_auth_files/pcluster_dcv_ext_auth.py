# Copyright 2019 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License"). You may not use this file except in compliance
# with the License. A copy of the License is located at
#
# http://aws.amazon.com/apache2.0/
#
# or in the "LICENSE.txt" file accompanying this file. This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES
# OR CONDITIONS OF ANY KIND, express or implied. See the License for the specific language governing permissions and
# limitations under the License.
from __future__ import print_function
from future.backports import OrderedDict
from future.backports.http.server import BaseHTTPRequestHandler, HTTPServer
from future.backports.socketserver import ThreadingMixIn
from future.backports.urllib.parse import parse_qsl, urlparse

import hashlib
import json
import os
import random
import re
import shutil
import ssl
import string
import subprocess
import sys
from collections import namedtuple
from datetime import datetime, timedelta
from pwd import getpwuid

import argparse

from retry.api import retry_call

AUTHORIZATION_FILE_DIR = "/var/spool/dcv_ext_auth"
LOG_FILE_PATH = "/var/log/parallelcluster/dcv_ext_auth.log"


def generate_random_token(token_length):
    """Generate CSPRNG compliant random tokens."""
    allowed_chars = "".join((string.ascii_letters, string.digits, "_", "-"))
    max_int = len(allowed_chars) - 1
    system_random = random.SystemRandom()

    return "".join(allowed_chars[system_random.randint(0, max_int)] for _ in range(token_length))


class OneTimeTokenHandler:
    """
    Store in memory tokens and information associated with them.

    The handler maintains a limited number of tokens in memory with a FIFO logic when the limits are reached.
    """

    def __init__(self, max_number_of_tokens):
        self._tokens = OrderedDict()
        self._max_number_of_tokens = max_number_of_tokens

    def add_token(self, token, token_info):
        """
        Add token and his corresponding information in the storage.

        :param token the token to store
        :param token_info a tuple of values associated to the token to store
        """
        while len(self._tokens) >= self._max_number_of_tokens:
            # Remove the first token stored
            self._tokens.popitem(last=False)

        self._tokens[token] = token_info

    def get_token_info(self, token):
        """Pop the token and return the related information if the token is present, else returns None."""
        return self._tokens.pop(token, None)


class DCVAuthenticator(BaseHTTPRequestHandler):
    """
    Simple HTTP server to handle NICE DCV authentication process.

    The authentication process to access to a DCV session is performed by the following steps:
    1. Obtain a Request Token:
    - an user declares himself and asks for a Request Token for a given DCV Session:
        - curl -X GET -G http://localhost:<port> -d action=requestToken -d authUser=<username> -d sessionID=<ID>
    - the authenticator will return a json containing requestToken and accessFile values:
        - the requestToken must be used as parameter for the Session Token request
        - the accessFile is used to verify the user identity in the Session Token request

    2. Obtain a DCV Session Token:
    - the user must create an "access file" in the AUTHORIZATION_FILE_DIR, named as the retrieved accessFile value
    - the user asks for a SessionToken (the real token to access to the DCV session)
        - curl -X GET -G http://localhost:<port> -d action=sessionToken -d requestToken=<tr>
    - the authenticator verifies the owner of the access file, the validity of the requestToken and returns
      a Session Token
    - the user can use the retrieved Session Token to connect to the DCV session.

    3. DCV connection:
    - the Session Token must be used in the web browser to access to the DCV Session
    - the DCV process, running in the same instance of the authenticator, will ask to validate the token:
        - curl -k http://localhost:<port> -d sessionId=<session-id> -d authenticationToken=<token>
    - the authenticator verifies the validity of the authenticationToken and permits the user to access to the session.
    """

    class IncorrectRequestException(Exception):
        pass

    USER_REGEX = r"^[a-z_]([a-z0-9_-]{0,31}|[a-z0-9_-]{0,30}\$)$"
    SESSION_ID_REGEX = r"^([a-zA-Z0-9_-]{0,128})$"
    TOKEN_REGEX = r"^([a-zA-Z0-9_-]{256})$"

    MAX_NUMBER_OF_REQUEST_TOKENS = 500
    MAX_NUMBER_OF_SESSION_TOKENS = 100
    REQUEST_TOKEN_EXPIRE_SECONDS = 10
    SESSION_TOKEN_EXPIRE_SECONDS = 30

    # Define the information associated to a specific token
    RequestTokenInfo = namedtuple("RequestTokenInfo", "user dcv_session_id creation_time access_file")
    SessionTokenInfo = namedtuple("SessionTokenInfo", "user dcv_session_id creation_time")

    # Define two token handlers with different capacity and expiration
    request_token_manager = OneTimeTokenHandler(max_number_of_tokens=MAX_NUMBER_OF_REQUEST_TOKENS)
    request_token_ttl = timedelta(seconds=REQUEST_TOKEN_EXPIRE_SECONDS)
    session_token_manager = OneTimeTokenHandler(max_number_of_tokens=MAX_NUMBER_OF_SESSION_TOKENS)
    session_token_ttl = timedelta(seconds=SESSION_TOKEN_EXPIRE_SECONDS)

    def do_GET(self):  # noqa N802
        """
        Handle GET requests coming from the user to obtain request and session tokens.

        The format of the request should be:
            curl -X GET -G http://localhost:<port> -d action=requestToken -d authUser=<username> -d sessionID=<ID>
            curl -X GET -G http://localhost:<port> -d action=sessionToken -d requestToken=<tr>
        """
        try:
            # validate number of parameters
            parameters = dict(parse_qsl(urlparse(self.path).query))
            if not parameters or len(parameters) > 3:
                raise DCVAuthenticator.IncorrectRequestException(
                    "Incorrect number of parameters passed.\nParameters: {0}".format(parameters)
                )

            # evaluate action parameter
            action = self._extract_parameters_values(parameters, ["action"])[0]
            if action == "requestToken":
                username, session_id = self._extract_parameters_values(parameters, ["authUser", "sessionID"])
                result = self._get_request_token(username, session_id)
            elif action == "sessionToken":
                request_token = self._extract_parameters_values(parameters, ["requestToken"])[0]
                result = self._get_session_token(request_token)
            else:
                raise DCVAuthenticator.IncorrectRequestException("The action specified is not correct")

            self._set_headers(400, content="application/json")
            self.wfile.write(result.encode())

        except DCVAuthenticator.IncorrectRequestException as e:
            self.log_message("ERROR: {0}".format(e))
            self._return_bad_request(e)

    def do_POST(self):  # noqa N802
        """
        Handle POST requests, coming from NICE DCV server.

        The format of the request is the following:
            curl -k http://localhost:<port> -d sessionId=<session-id> -d authenticationToken=<token>
        """
        try:
            length = int(self.headers["Content-Length"])
            field_data = self.rfile.read(length).decode("utf-8")
            parameters = dict(parse_qsl(field_data))
            if len(parameters) != 3:
                raise DCVAuthenticator.IncorrectRequestException(
                    "Incorrect number of parameters passed.\nParameters: {0}".format(parameters)
                )
            session_token, session_id = self._extract_parameters_values(
                parameters, ["authenticationToken", "sessionId"]
            )

            authorized_user = self._check_auth(session_id, session_token)
            if authorized_user:
                self._return_auth_ok(username=authorized_user)
            else:
                raise DCVAuthenticator.IncorrectRequestException("The session token is not valid")

        except DCVAuthenticator.IncorrectRequestException as e:
            self.log_message("ERROR: {0}".format(e))
            self._return_auth_ko(e)

    def log_message(self, formatting, *args):
        self.server.log_file.write(
            "{0} - - [{1}] {2}\n".format(self.address_string(), datetime.utcnow(), formatting % args)
        )
        self.server.log_file.flush()

    def _set_headers(self, response, content="text/xml", length=None):
        self.send_response(response)
        self.send_header("Content-type", content)
        if length:
            self.send_header("Content-Length", length)
        self.end_headers()

    def _return_auth_ko(self, message):
        http_string = '<auth result="no"><message>{0}</message></auth>'.format(message)
        self._set_headers(200, length=len(http_string))
        self.wfile.write(http_string.encode())

    def _return_auth_ok(self, username):
        http_string = '<auth result="yes"><username>{0}</username></auth>'.format(username)
        self._set_headers(200, length=len(http_string))
        self.wfile.write(http_string.format(username).encode())

    def _return_bad_request(self, message):
        self._set_headers(200)
        self.wfile.write("{0}\n".format(message).encode())

    @staticmethod
    def _extract_parameters_values(parameters, keys):
        try:
            return [parameters[key] for key in keys]
        except KeyError:
            raise DCVAuthenticator.IncorrectRequestException(
                "Incorrect parameters for the request token\nThey should be {0}".format(", ".join(keys))
            )

    @classmethod
    def _check_auth(cls, session_id, session_token):
        """Check session token expiration to see if it is still valid for the given DCV session id."""

        # validate session and session token
        DCVAuthenticator._validate_param(session_id, DCVAuthenticator.SESSION_ID_REGEX, "sessionId")
        DCVAuthenticator._validate_param(session_token, DCVAuthenticator.TOKEN_REGEX, "sessionToken")

        # search for token in the internal authenticator token storage
        token_info = cls.session_token_manager.get_token_info(session_token)
        if (
            token_info
            and token_info.dcv_session_id == session_id
            and datetime.utcnow() - token_info.creation_time <= cls.session_token_ttl
        ):
            return token_info.user

    @classmethod
    def _get_request_token(cls, user, session_id):
        """
        Obtain the request token and the "access file" name required to obtain the session token.

        Generate a Request token, store in memory and returns a json containing the token itself
        and the name of the file the user must create in the AUTHORIZATION_FILE_DIR.
        """
        # validate user and session
        DCVAuthenticator._validate_param(user, DCVAuthenticator.USER_REGEX, "authUser")
        DCVAuthenticator._validate_param(session_id, DCVAuthenticator.SESSION_ID_REGEX, "sessionId")
        DCVAuthenticator._verify_session_existence(user, session_id)

        # create and register internally a request token to use to retrieve the session token
        request_token = generate_random_token(256)
        access_file = generate_sha512_hash(request_token)
        cls.request_token_manager.add_token(
            request_token, DCVAuthenticator.RequestTokenInfo(user, session_id, datetime.utcnow(), access_file)
        )

        return json.dumps({"requestToken": request_token, "accessFile": access_file})

    @classmethod
    def _get_session_token(cls, request_token):
        """
        Obtain the session token to connect to the DCV session.

        Generate a Session token, store in memory and returns a json containing the token itself.
        """
        DCVAuthenticator._validate_param(request_token, DCVAuthenticator.TOKEN_REGEX, "requestToken")

        # retrieve request token information to validate it
        token_info = cls.request_token_manager.get_token_info(request_token)
        if not token_info:
            raise DCVAuthenticator.IncorrectRequestException("The requestToken parameter is not valid")
        user = token_info.user
        session_id = token_info.dcv_session_id
        access_file = token_info.access_file

        # verify token expiration
        if datetime.utcnow() - token_info.creation_time > cls.request_token_ttl:
            raise DCVAuthenticator.IncorrectRequestException("The requestToken is not valid anymore")

        # verify user by checking if the access_file is created by the user asking the session token
        try:
            access_file_path = "{0}/{1}".format(AUTHORIZATION_FILE_DIR, access_file)
            file_details = os.stat(access_file_path)
            if getpwuid(file_details.st_uid).pw_name != user:
                raise DCVAuthenticator.IncorrectRequestException("The user is not the one that created the access file")
            if datetime.utcnow() - datetime.utcfromtimestamp(file_details.st_mtime) > cls.request_token_ttl:
                raise DCVAuthenticator.IncorrectRequestException("The access file has expired")
            os.remove(access_file_path)
        except OSError:
            raise DCVAuthenticator.IncorrectRequestException("The access file does not exist")

        # create and register internally a session token
        DCVAuthenticator._verify_session_existence(user, session_id)
        session_token = generate_random_token(256)
        cls.session_token_manager.add_token(
            session_token, DCVAuthenticator.SessionTokenInfo(user, session_id, datetime.utcnow())
        )

        return json.dumps({"sessionToken": session_token})

    @staticmethod
    def _validate_param(string_to_test, regex, resource_name):
        if not re.match(regex, string_to_test):
            raise DCVAuthenticator.IncorrectRequestException("The {0} parameter is not valid".format(resource_name))

    @staticmethod
    def _is_session_valid(user, session_id):
        """
        Verify if the DCV session exists and the ownership.

        # We are using ps aux to retrieve the list of sessions
        # because currently DCV doesn't allow list-session to list all session even for non-root user.
        # TODO change this method if DCV updates his behaviour.
        """
        # Remove the first and the last because they are the heading and empty, respectively
        processes = subprocess.check_output(["ps", "aux"]).decode("utf-8").split("\n")[1:-1]

        # Check the filter is empty
        if not next(filter(lambda x: DCVAuthenticator.is_process_valid(x, user, session_id), processes), None):
            raise DCVAuthenticator.IncorrectRequestException("The given session for the user does not exists")

    @staticmethod
    def _verify_session_existence(user, session_id):
        retry_call(DCVAuthenticator._is_session_valid, fargs=[user, session_id], tries=5, delay=1)

    @staticmethod
    def is_process_valid(row, user, session_id):
        # row example:
        # centos 63 0.0 0.0 4348844 3108   ??  Ss   23Jul19   2:32.46  /usr/libexec/dcv/dcvagent --session-id mysession
        fields = row.split()
        command_index = 10
        session_name_index = 12
        user_index = 0
        dcv_agent_path = "/usr/libexec/dcv/dcvagent"

        return (
            fields[command_index] == dcv_agent_path
            and fields[user_index] == user
            and fields[session_name_index] == session_id
        )


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """Handle requests in a separate thread."""


def _run_server(port, certificate=None, key=None):
    """
    This class run the external authenticator server on localhost.

    The external authenticator server *must* run with an appropriate user.

    :param port: the port in which you want to start the server
    :param certificate: the certificate to use if https
    :param key: the key to use if https and it's not in certificate
    """
    server_address = ("localhost", port)
    httpd = ThreadedHTTPServer(server_address, DCVAuthenticator)

    # set server logging
    log_file = open(LOG_FILE_PATH, "w")
    os.chmod(LOG_FILE_PATH, 0o644)
    httpd.log_file = log_file

    if certificate:
        if key:
            httpd.socket = ssl.wrap_socket(httpd.socket, certfile=certificate, keyfile=key, server_side=True)
        else:
            httpd.socket = ssl.wrap_socket(httpd.socket, certfile=certificate, server_side=True)
    print(
        "Starting DCV external authenticator {PROTOCOL} server on port {PORT}, use <Ctrl-C> to stop".format(
            PROTOCOL="HTTPS" if certificate else "HTTP", PORT=port
        )
    )
    httpd.serve_forever()


def _parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="Execute the ParallelCluster DCV External Authenticator")
    parser.add_argument("--port", help="The port in which you want to start the HTTP server", type=int)
    parser.add_argument(
        "--certificate", help="The certificate to use to run in HTTPS. It must be a .pem file"
    )
    parser.add_argument("--key", help="The .key of the certificate, if not included in it.")
    return parser.parse_args()


def generate_sha512_hash(*args):
    """Generate a salted sha512 of the given token."""
    salt = generate_random_token(256)

    hash_handler = hashlib.sha512()
    for item in args, salt:
        hash_handler.update(str(item).encode("utf-8"))

    return hash_handler.hexdigest()


def main():
    try:
        args = _parse_args()
        # clean up the directory containing old files
        shutil.rmtree(AUTHORIZATION_FILE_DIR, ignore_errors=True)
        _run_server(port=args.port if args.port else 8444, certificate=args.certificate, key=args.key)
    except KeyboardInterrupt:
        print("Closing the server")
    except Exception as e:
        print("Unexpected error of type {0}: {1}".format(type(e).__name__, e))
        sys.exit(1)


if __name__ == "__main__":
    main()
