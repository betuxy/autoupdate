#!/bin/bash
set -e

# Install the Ansible SSH public key on every start (key is bind-mounted at runtime)
if [ -f /tmp/ansible_key.pub ]; then
    cat /tmp/ansible_key.pub > /home/ansible/.ssh/authorized_keys
    chmod 600 /home/ansible/.ssh/authorized_keys
    chown ansible:ansible /home/ansible/.ssh/authorized_keys
fi

# Generate SSH host keys if missing
ssh-keygen -A

exec /usr/sbin/sshd -D -e
