FROM postgres:15.5-bullseye

ARG OPENBIS_DISTRIBUTABLE_ARG
ARG HOST_USER_UID_ARG
ARG HOST_USER_GID_ARG

ENV OPENBIS_REPO https://wiki-bsse.ethz.ch/download/attachments/11109400/
ENV OPENBIS_DISTRIBUTABLE ${OPENBIS_DISTRIBUTABLE_ARG:-openBIS-installation-standard-technologies-18.06.2-r1541498074}
ENV ADMIN_PASSWORD changeit
ENV ETLSERVER_PASSWORD changeit
ENV HOST_USER_UID ${HOST_USER_UID_ARG:-1000}
ENV HOST_USER_GID ${HOST_USER_GID_ARG:-1000}

ENV JAVA_HOME /usr/lib/jvm/java-17-openjdk-amd64

## User creation
RUN groupadd -r openbis --gid=$HOST_USER_GID && \
	useradd -r -g openbis --uid=$HOST_USER_UID --gid=$HOST_USER_GID openbis && \
	mkdir -p /home/openbis && chown -R openbis:openbis /home/openbis
EXPOSE 443
EXPOSE 2222

## Copy openBIS Tarball
COPY "$OPENBIS_DISTRIBUTABLE".tar.gz /home/openbis/"$OPENBIS_DISTRIBUTABLE".tar.gz

RUN apt-get update && \
	# prevent postgres packages from being upgraded
	apt-mark hold postgresql-* && \
	# upgrade all installed packages
	apt-get upgrade -y && \
	# UNZIP used by openbis Installer
	apt-get install -y unzip net-tools curl && \
	# LESS used by openbis scripts
	apt-get install -y less && \
    # git is used to fetch the ELN LIMS Life Sciences master data
	apt-get install -y git && \
	# apt-utils is needed to remove the warning "debconf: delaying package configuration, since apt-utils is not installed"
	apt-get install -y apt-utils && \
	# Apache installation used by openbis
	apt-get install -y apache2 libapache2-mod-wsgi-py3 python-dev && \
	# Install JRE
	apt-get remove -y openjdk-11* && \
	apt-get install -y openjdk-17-jre && \
	# openbis and store directory
	mkdir -p /home/openbis/openbis && chown -R openbis /home/openbis/openbis && \
	mkdir -p /home/openbis/store && chown -R openbis /home/openbis/store && \
	mkdir -p /home/openbis/openbis/data && chown -R openbis /home/openbis/openbis/data && \
	# Unzip tarball
	tar -xvzf /home/openbis/"$OPENBIS_DISTRIBUTABLE".tar.gz -C /home/openbis/ && \
	# Delete tarball
	rm -rf /home/openbis/"$OPENBIS_DISTRIBUTABLE".tar.gz && \
	# Updating the installer console.properties to install with sensible defaults
	sed -i "s/^INSTALL_PATH.*/INSTALL_PATH= \/home\/openbis\/openbis\//g" /home/openbis/"$OPENBIS_DISTRIBUTABLE"/console.properties && \
	sed -i "s/^DSS_ROOT_DIR.*/DSS_ROOT_DIR= \/home\/openbis\/store\//g" /home/openbis/"$OPENBIS_DISTRIBUTABLE"/console.properties && \
	sed -i "s/^INSTALLATION_TYPE.*/INSTALLATION_TYPE=server/g" /home/openbis/"$OPENBIS_DISTRIBUTABLE"/console.properties && \
	sed -i "s/^#ELN-LIMS.*/ELN-LIMS = true/g" /home/openbis/"$OPENBIS_DISTRIBUTABLE"/console.properties && \
	sed -i "s/^#ELN-LIMS-MASTER-DATA.*/ELN-LIMS-MASTER-DATA = false/g" /home/openbis/"$OPENBIS_DISTRIBUTABLE"/console.properties && \
	sed -i "s/^#PATHINFO_DB_ENABLED.*/PATHINFO_DB_ENABLED = true/g" /home/openbis/"$OPENBIS_DISTRIBUTABLE"/console.properties && \
	sed -i "s/^#ELN-LIMS-LIFE-SCIENCES.*/ELN-LIMS-LIFE-SCIENCES = true/g" /home/openbis/"$OPENBIS_DISTRIBUTABLE"/console.properties && \
	ls -hal /home/openbis/"$OPENBIS_DISTRIBUTABLE"/console.properties && \
	cat /home/openbis/"$OPENBIS_DISTRIBUTABLE"/console.properties && \
	# running installation
	su openbis -c /home/openbis/"$OPENBIS_DISTRIBUTABLE"/run-console.sh && \
	# Path AS service.properties to store config files in folders that are keep in the state
	sed -i "s/^# xls-import.version-data-file.*/xls-import.version-data-file= \/home\/openbis\/openbis\/servers\/openBIS-server\/jetty\/etc\/xls-import-version-info.json/g" /home/openbis/openbis/servers/openBIS-server/jetty/etc/service.properties && \
	# Path AS service.properties to allow cross site access to the API
	sed -i "s/^#trusted-cross-origin-domains.*/trusted-cross-origin-domains= */g" /home/openbis/openbis/servers/openBIS-server/jetty/etc/service.properties && \
	# Path AS service.properties to allow 5 sessions per user instead the default
	sed -i "s/^#max-number-of-sessions-per-user.*/max-number-of-sessions-per-user = 5/g" /home/openbis/openbis/servers/openBIS-server/jetty/etc/service.properties && \
	# Path DSS service.properties since contains an unknown host for localhost
	sed -i "s/^host-address.*/host-address = http:\/\/localhost/g" /home/openbis/openbis/servers/datastore_server/etc/service.properties && \
	sed -i "s/^# ftp.server.enable = true.*/ftp.server.enable = true/g" /home/openbis/openbis/servers/datastore_server/etc/service.properties && \
	sed -i "s/^# ftp.server.sftp-port = 2222.*/ftp.server.sftp-port = 2222/g" /home/openbis/openbis/servers/datastore_server/etc/service.properties && \
	sed -i "s/^openBISUrl.*/openBISUrl = http:\/\/localhost:8080/g" /home/openbis/openbis/servers/afs-server/etc/service.properties && \
	# Delete core-plugins.properties, which will be created during runtime
	rm /home/openbis/openbis/servers/core-plugins/core-plugins.properties && \
	# Delete unzipped installer
	rm -rf /home/openbis/"$OPENBIS_DISTRIBUTABLE" && \
	# Delete blast
	rm -rf /home/openbis/openbis/servers/core-plugins/eln-lims/bin/blast && \
	# Session workspace created manualy to link the directory outside of the container
	mkdir -p /home/openbis/openbis/servers/datastore_server/data/sessionWorkspace && chown -R openbis /home/openbis/openbis/servers/datastore_server/data/sessionWorkspace && \
	# openBIS/DSS configuration for jetty/apache
	mv /home/openbis/openbis/servers/openBIS-server/jetty/start.d/https.ini /home/openbis/openbis/servers/openBIS-server/jetty/start.d/https.ini.backup && \
	mv /home/openbis/openbis/servers/openBIS-server/jetty/start.d/ssl.ini /home/openbis/openbis/servers/openBIS-server/jetty/start.d/ssl.ini.backup && \
	# Miscelaneous plugin.properties
	sed -i 's/^# file-server.*/file-server.repository-path = \/home\/openbis\/openbis_state\/as_data\/file-server/g' /home/openbis/openbis/servers/core-plugins/eln-lims/1/as/miscellaneous/file-service/plugin.properties && \
	# Patch DSS service.properties to run under http
	sed -i 's/8444/8081/g' /home/openbis/openbis/servers/datastore_server/etc/service.properties && \
	sed -i 's/8443/8080/g' /home/openbis/openbis/servers/datastore_server/etc/service.properties && \
	sed -i 's/^use-ssl.*/use-ssl = false/g' /home/openbis/openbis/servers/datastore_server/etc/service.properties && \
	sed -i "s/^download-url.*/download-url = /g" /home/openbis/openbis/servers/datastore_server/etc/service.properties && \
	a2enmod ssl; a2enmod proxy; a2enmod rewrite; a2enmod proxy_http && \
	mkdir /etc/pki/ && \
	mkdir /etc/pki/tls/ && \
	openssl req -new -x509 -nodes -days 365 -newkey rsa:2048 -subj "/C=SW/ST=Basel-City/L=Basel/O=SSDM/CN=SIS" -keyout /etc/pki/tls/apache.key -out /etc/pki/tls/apache.crt && \
	ln -s /etc/apache2/conf-available/openbis.conf   /etc/apache2/conf-enabled/openbis.conf && \
	sed -i '1 i\ServerName localhost' /etc/apache2/apache2.conf

# openBIS Jetty patch for apache
COPY http.ini /home/openbis/openbis/servers/openBIS-server/jetty/start.d/
# openBIS config for apache
COPY openbis.conf /etc/apache2/conf-available/openbis.conf
# Template for openBIS state on the same directory (used by the startup script)
COPY openbis_state_template.zip /home/openbis/
# Entrypoint - Just a patched version of the postgres image entry point adding the openBIS startup
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

STOPSIGNAL SIGTERM
