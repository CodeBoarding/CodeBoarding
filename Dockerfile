FROM ubuntu:22.04
RUN apt-get update && \
    apt-get install -y --no-install-recommends software-properties-common gnupg && \
    add-apt-repository ppa:deadsnakes/ppa && \
    apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        ccache \
        cmake \
        curl \
        vim \
        git \
        python3.11 \
        python3-venv \
        openssh-client \
        libjpeg-dev \
        libpng-dev && \
    rm -rf /var/lib/apt/lists/*

RUN apt-get remove --purge -y nodejs libnode72 libnode-dev npm || true

RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y nodejs && \
    node -v && npm -v && \
    rm -rf /var/lib/apt/lists/*

RUN /usr/sbin/update-ccache-symlinks
RUN mkdir /opt/ccache && ccache --set-config=cache_dir=/opt/ccache  

RUN curl -sS https://bootstrap.pypa.io/get-pip.py | python3.11

RUN ln -s /usr/local/bin/pip3.11 /usr/bin/pip3.11
RUN pip3.11 install --upgrade uv

RUN update-alternatives --install /usr/bin/python python /usr/bin/python3.11 1
RUN update-alternatives --install /usr/bin/pip pip /usr/bin/pip3.11 1
RUN update-alternatives --install /usr/bin/uv uv /usr/local/bin/uv 1

# Create working directory
WORKDIR /app

# Create .ssh directory with correct permissions
RUN mkdir -p /root/.ssh && chmod 700 /root/.ssh

# Copy code to container
COPY . /app

# Add entrypoint script
COPY docker-entrypoint.sh /
RUN chmod +x /docker-entrypoint.sh
ENTRYPOINT ["/docker-entrypoint.sh"]

# Default to bash for interactive/manual entry
CMD ["/bin/bash"]
