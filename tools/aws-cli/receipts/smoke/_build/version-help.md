# Stage B — first in-container execution (build/capability evidence)
Image: amazon/aws-cli:2.36.1 @ sha256:406ca32d31e640a56e8d52921b40528cc64bfa59ec9cb4ee1456db6746cb7292
Captured: 2026-07-17T08:07:59Z  arch=aarch64  emulated=no (native arm64)

## aws --version
aws-cli/2.36.1 Python/3.14.6 Linux/6.17.0-1020-gcp docker/aarch64.amzn.2023

## docker inspect entrypoint/arch
Entrypoint=["/usr/local/bin/aws"] Arch=arm64 Os=linux
===== aws s3api list-objects-v2 help (key flags) =====
LIST-OBJECTS-V2()					     LIST-OBJECTS-V2()
       list-objects-v2 -
	    list-objects-v2
							     LIST-OBJECTS-V2()
===== aws s3api list-objects help (key flags) =====
LIST-OBJECTS()							LIST-OBJECTS()
       list-objects -
	    list-objects
								LIST-OBJECTS()
===== aws s3api list-object-versions help (key flags) =====
===== aws s3 ls help (key flags) =====
	      --recursive
	      --recursive \
