set -x
cd /tmp
cp -r /src ps3build
cd ps3build
rm -f pS3.0-1-16
go mod init pS3
go get github.com/aws/aws-sdk-go@v1.44.249
go get github.com/spf13/cobra@v1.7.0
go get github.com/spf13/viper@v1.15.0
go get golang.org/x/net/http2
echo "=== go build ./... ==="
go build ./... 
echo "=== go vet ./cmd (exit $?) ==="
go vet ./cmd 2>&1
