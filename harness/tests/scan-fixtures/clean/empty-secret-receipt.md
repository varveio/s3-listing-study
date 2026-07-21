## Invocation

```sh
docker run -d --network s3-listing-study-subjects --cap-drop ALL --security-opt no-new-privileges:true -e AWS_EC2_METADATA_DISABLED=true -e TZ=UTC -e AWS_ACCESS_KEY_ID= -e AWS_SECRET_ACCESS_KEY= -e AWS_SESSION_TOKEN= amazon/aws-cli@sha256:eb85 s3api list-objects-v2
```

The empty `-e AWS_SECRET_ACCESS_KEY=` above is the wrapper's credential
starvation made visible, not a leak.
