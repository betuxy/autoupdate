#!/bin/bash
set -e

CN="icinga-master"
CERT_DIR="/var/lib/icinga2/certs"
CA_DIR="/var/lib/icinga2/ca"

# Set zone/node names in constants
sed -i "s|^const NodeName = .*|const NodeName = \"$CN\"|" /etc/icinga2/constants.conf
sed -i "s|^const ZoneName = .*|const ZoneName = \"master\"|" /etc/icinga2/constants.conf

# Generate PKI on first start only
if [ ! -f "$CERT_DIR/$CN.crt" ]; then
    mkdir -p "$CERT_DIR" "$CA_DIR"

    icinga2 pki new-ca

    icinga2 pki new-cert \
        --cn "$CN" \
        --key "$CERT_DIR/$CN.key" \
        --csr "$CERT_DIR/$CN.csr"

    icinga2 pki sign-csr \
        --csr "$CERT_DIR/$CN.csr" \
        --cert "$CERT_DIR/$CN.crt"

    cp "$CA_DIR/ca.crt" "$CERT_DIR/ca.crt"
    chown -R nagios:nagios "$CERT_DIR" "$CA_DIR"
    chmod 700 "$CA_DIR"
fi

exec icinga2 daemon --log-level information
