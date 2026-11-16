FROM scratch

# Complete offline Linux root filesystem bundled with this marketplace plugin.
# No registry access is required: PrimiGenius builds the image locally on first use.
ADD runtime/rootfs.tar.gz /

LABEL org.opencontainers.image.title="PrimiGenius SRA FASTQ Auto Layout Runtime" \
      org.opencontainers.image.version="2.0.0" \
      org.opencontainers.image.description="NCBI SRA Toolkit 3.4.1, Python and pigz for automatic SINGLE/PAIRED FASTQ generation"

ENV PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
    HTTP_PROXY="" \
    HTTPS_PROXY="" \
    ALL_PROXY="" \
    http_proxy="" \
    https_proxy="" \
    all_proxy="" \
    PYTHONUNBUFFERED="1"

WORKDIR /workspace
CMD ["/bin/sh"]

