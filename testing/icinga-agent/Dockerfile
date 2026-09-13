FROM debian:13

ENV DEBIAN_FRONTEND=noninteractive

# Add Icinga2 repository then install everything in one layer
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates gnupg \
    && curl -sSL https://packages.icinga.com/icinga.key \
        | gpg --dearmor > /usr/share/keyrings/icinga.gpg \
    && printf 'deb [signed-by=/usr/share/keyrings/icinga.gpg] https://packages.icinga.com/debian icinga-trixie main\n' \
        > /etc/apt/sources.list.d/icinga.list \
    && apt-get update && apt-get install -y --no-install-recommends \
        systemd \
        systemd-sysv \
        dbus \
        icinga2 \
        nagios-plugins-basic \
        openssh-server \
        python3 \
        python3-apt \
        sudo \
        iproute2 \
        needrestart \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Mask units that don't function inside a container
RUN systemctl mask \
        getty@tty1.service \
        console-getty.service \
        systemd-udevd.service \
        systemd-udev-trigger.service \
        systemd-modules-load.service \
        sys-fs-fuse-connections.mount \
        systemd-remount-fs.service \
        dev-hugepages.mount \
        sys-kernel-config.mount \
        e2scrub_reap.service \
        fstrim.timer \
    2>/dev/null || true

# Ansible user with passwordless sudo
RUN useradd -m -s /bin/bash ansible \
    && echo 'ansible ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/ansible \
    && mkdir -p /home/ansible/.ssh \
    && chown ansible:ansible /home/ansible/.ssh \
    && chmod 700 /home/ansible/.ssh

# SSH server config
RUN sed -i 's/#*PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config \
    && sed -i 's/#*PubkeyAuthentication.*/PubkeyAuthentication yes/' /etc/ssh/sshd_config \
    && sed -i 's/#*PermitEmptyPasswords.*/PermitEmptyPasswords no/' /etc/ssh/sshd_config

# Allow nagios to run needrestart (check_needrestart runs as nagios via icinga2)
RUN echo 'nagios ALL=(root) NOPASSWD: /usr/sbin/needrestart' > /etc/sudoers.d/nagios-needrestart

# Remove default icinga2 conf.d (host/service objects the agent doesn't own)
# and replace with only the check command definitions
RUN rm -f /etc/icinga2/conf.d/*.conf
COPY check-commands.conf /etc/icinga2/conf.d/

# Custom check script
COPY check_needrestart /usr/lib/nagios/plugins/check_needrestart
RUN chmod +x /usr/lib/nagios/plugins/check_needrestart

# Setup scripts run by systemd on each boot
COPY setup-ansible-key.service  /etc/systemd/system/
COPY setup-icinga-agent.service /etc/systemd/system/
COPY setup-icinga-agent.sh      /usr/local/sbin/setup-icinga-agent.sh
RUN chmod +x /usr/local/sbin/setup-icinga-agent.sh \
    && systemctl enable setup-ansible-key.service setup-icinga-agent.service icinga2.service

# Override reboot/shutdown: kill PID 1 so Docker restart policy brings the container back up.
# Systemd handles SIGTERM as a controlled shutdown, which exits the container cleanly.
RUN printf '#!/bin/sh\nkill 1\n' | tee /sbin/reboot /sbin/shutdown > /dev/null \
    && chmod +x /sbin/reboot /sbin/shutdown

EXPOSE 22 5665

STOPSIGNAL SIGRTMIN+3

CMD ["/sbin/init"]
