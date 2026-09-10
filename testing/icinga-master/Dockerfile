FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

# Install Icinga2 from official repo
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        gnupg \
        ca-certificates \
        monitoring-plugins-basic \
    && curl -sSL https://packages.icinga.com/icinga.key \
       | gpg --dearmor -o /etc/apt/keyrings/icinga.gpg \
    && echo "deb [signed-by=/etc/apt/keyrings/icinga.gpg] https://packages.icinga.com/ubuntu icinga-noble main" \
       > /etc/apt/sources.list.d/icinga.list \
    && apt-get update && apt-get install -y --no-install-recommends icinga2 \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Enable API feature
RUN icinga2 feature enable api

# Inject configuration
COPY conf/api-users.conf /etc/icinga2/conf.d/api-users.conf
COPY conf/zones.conf /etc/icinga2/zones.conf
RUN mkdir -p /etc/icinga2/zones.d/master
COPY conf/hosts.conf /etc/icinga2/zones.d/master/hosts.conf

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 5665

CMD ["/entrypoint.sh"]
