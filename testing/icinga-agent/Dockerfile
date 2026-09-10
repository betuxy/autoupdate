FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        openssh-server \
        python3 \
        python3-apt \
        sudo \
        iproute2 \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Ansible user with passwordless sudo
RUN useradd -m -s /bin/bash ansible \
    && echo 'ansible ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/ansible \
    && mkdir -p /home/ansible/.ssh \
    && chown ansible:ansible /home/ansible/.ssh \
    && chmod 700 /home/ansible/.ssh

# SSH server setup
RUN mkdir /var/run/sshd \
    && sed -i 's/#*PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config \
    && sed -i 's/#*PubkeyAuthentication.*/PubkeyAuthentication yes/' /etc/ssh/sshd_config \
    && sed -i 's/#*PermitEmptyPasswords.*/PermitEmptyPasswords no/' /etc/ssh/sshd_config

# Override reboot/shutdown: kill PID 1 so Docker restart policy brings the container back up.
# This lets ansible.builtin.reboot work correctly against Docker containers.
RUN printf '#!/bin/sh\nkill 1\n' | tee /sbin/reboot /sbin/shutdown > /dev/null \
    && chmod +x /sbin/reboot /sbin/shutdown

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 22

CMD ["/entrypoint.sh"]
