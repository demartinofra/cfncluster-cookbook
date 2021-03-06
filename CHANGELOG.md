aws-parallelcluster-cookbook CHANGELOG
======================================

This file is used to list changes made in each version of the AWS ParallelCluster cookbook.

2.1.1
-----

- China Regions, cn-north-1 and cn-northwest-1 support

2.1.0
-----

- EFS support
- RAID support

2.0.2
-----

- Fix issue with jq on ubuntu1404 and centos6. Now using version 1.4
- Fix dependency issue with AWS CLI package on ubuntu1404

2.0.0
-----

- Rename CfnCluster to AWS ParallelCluster
- Support multiple EBS Volumes
- Add AWS Batch as a supported scheduler
- Support Custom AMI's


1.6.0
-----

- Add `scaledown_idletime` to nodewatcher config
- Add cookbook recipes for jobwatcher
- Remove publish_pending scripts


1.5.4
-----

- Set SGE Accounting summary to be true, this reports a single accounting record
for a mpi job
- Add option to disable ganglia `extra_json = { "cfncluster" : { "ganglia_enabled" : "no" } }`


1.5.2
-----

- Fix bug that prevented c5d/m5d instances from working
- Set CPU as a consumable resource in slurm config

1.5.1
-----

Major new features/updates:

  - Added parameter to specify custom cfncluster-node package

Bug fixes/minor improvements:

  - Fixed poise-python dependecy issue
  - Poll on EBS Volume attachment status
  - Added more info on failure of pre and post install files
  - Fixed SLURM cron job to publish pending metric

1.4.1
-----

Major new features/updates:

  - Updated to latest cfncluster-node 1.4.3

1.4.0
-----

Major new features/updates:

  - Updated to Amazon Linux 2017.09.1
  - Applied patches to Ubuntu 16.04
  - Applied patches to Ubuntu 14.04
  - Updated to Centos 7.4
  - Upgraded Centos 6 AMI
  - Updated to Nvidia driver 384
  - Updated to CUDA 9
  - Updated to latest cfncluster-node 1.4.2

Bug fixes/minor improvements:

  - Added support for NVMe-based instance store
  - Fixed ganglia plotting issue on ubuntu
  - Fixed slow SLURM scaling times on systemd platforms.

1.3.2
-----
  - Relicensed to Apache License 2.0
  - Updated to Amazon Linux 2017.03
  - Pulled in latest cookbook dependencies
  - Removed Openlava support

1.2.0
-----
- Dougal Ballantyne <dougalb at amazon dot com>
  - Updated to Chef 12.8.1
  - Updated Openlava to 3.1.3
  - Updated SGE to 8.1.9
  - Updated cfncluster-node to 1.1.0
  - Added slots to compute-ready script
  - Updated cookbook dependencies

1.1.0
-----
- Dougal Ballantyne <dougalb at amazon dot com> - Updated to Amazon Linux 2015.09.2 for base AMI

1.0.1
-----
- Dougal Ballantyne <dougalb at amazon dot com>
  - Fix Ganglia rebuild on 2nd run
  - Update to cfncluster-node==1.0.1

1.0.0
-----
- Dougal Ballantyne <dougalb at amazon dot com> - 1.0.0 release of cookbook matching 1.0.0 release of cfncluster.

0.1.0
-----
- Dougal Ballantyne <dougalb at amazon dot com> - Initial release of cfncluster-cookbooks
