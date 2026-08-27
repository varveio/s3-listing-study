#!/bin/sh
# Prepare the isolated bridge required by real-S3 Docker subjects.
set -eu

network=s3-listing-study-subjects
bridge=s3study0

if ! docker network inspect "$network" >/dev/null 2>&1; then
    interface=$(ip route show default | awk 'NR == 1 { print $5 }')
    test -n "$interface"
    mtu=$(cat "/sys/class/net/$interface/mtu")
    docker network create --driver bridge \
        --subnet 172.30.0.0/24 --gateway 172.30.0.1 \
        --opt "com.docker.network.bridge.name=$bridge" \
        --opt com.docker.network.bridge.enable_icc=false \
        --opt "com.docker.network.driver.mtu=$mtu" \
        --ipv6=false "$network" >/dev/null
fi

# Containers need public egress, but neither host services, cloud metadata nor
# private networks. -C makes repeated setup idempotent.
if ! sudo -n iptables -C INPUT -i "$bridge" -j REJECT 2>/dev/null; then
    sudo -n iptables -I INPUT 1 -i "$bridge" -j REJECT
fi
for destination in 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 169.254.0.0/16; do
    if ! sudo -n iptables -C DOCKER-USER -i "$bridge" -d "$destination" -j REJECT \
        2>/dev/null; then
        sudo -n iptables -I DOCKER-USER 1 -i "$bridge" -d "$destination" -j REJECT
    fi
done

echo "ready: $network on $bridge"
