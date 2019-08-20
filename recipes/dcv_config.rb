#
# Cookbook Name:: aws-parallelcluster
# Recipe:: dcv_config
#
# Copyright 2013-2016 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License"). You may not use this file except in compliance with the
# License. A copy of the License is located at
#
# http://aws.amazon.com/apache2.0/
#
# or in the "LICENSE.txt" file accompanying this file. This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES
# OR CONDITIONS OF ANY KIND, express or implied. See the License for the specific language governing permissions and
# limitations under the License.

# placeholderfunction
# Based on this function, a setting that disable graphic acceleration will be written into the dcv conf file.
# It should be disabled for graphic instance. For non graphic instance is needed to avoid bug
def is_graphic_instance()
  get_instance_type

  false
end

if node['platform'] == 'centos' && node['platform_version'].to_i == 7 && node['cfncluster']['cfn_node_type'] == "MasterServer"
  node.default['cfncluster']['dcv']['is_graphic_instance'] = is_graphic_instance

  cookbook_file "/etc/parallelcluster/generate_certificate.sh" do
    source 'ext_auth_files/generate_certificate.sh'
    owner 'root'
    mode '0700'
  end

  execute "certificate generation" do
    command "/etc/parallelcluster/generate_certificate.sh \"#{node['cfncluster']['dcv']['certificate']}\" #{node['cfncluster']['dcv']['ext_auth_user']} dcv"
    user 'root'
  end

  #Override dcv.conf file
  template "/etc/dcv/dcv.conf" do
    action :create
    source 'dcv.conf.erb'
    owner 'root'
    group 'root'
    mode '0755'
  end

  directory '/var/spool/dcv_ext_auth' do
    owner node['cfncluster']['dcv']['ext_auth_user']
    mode '1733'
    recursive true
  end

  directory '/var/log/parallelcluster/' do
    owner 'root'
    mode '1777'
    recursive true
  end

  cookbook_file "#{node['cfncluster']['dcv']['ext_auth_user_home']}/pcluster_dcv_ext_auth.py" do
    source 'ext_auth_files/pcluster_dcv_ext_auth.py'
    owner node['cfncluster']['dcv']['ext_auth_user']
    mode '0700'
  end

  service "dcvserver" do
    action [:start, :enable]
  end
end