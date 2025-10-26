#!/bin/bash
set -e

# Handle SSH keys if mounted
if [ -d "/tmp/host_ssh" ] && [ "$(ls -A /tmp/host_ssh 2>/dev/null)" ]; then
    echo "Setting up SSH keys..."
    
    # Ensure .ssh directory exists with correct permissions
    mkdir -p /root/.ssh
    chmod 700 /root/.ssh
    
    # Copy all files from mounted directory
    cp -r /tmp/host_ssh/* /root/.ssh/ 2>/dev/null || true
    
    # Set correct permissions
    chmod 700 /root/.ssh
    
    # Private keys should be 600
    find /root/.ssh -type f -name "id_*" ! -name "*.pub" -exec chmod 600 {} \; 2>/dev/null || true
    
    # Public keys should be 644
    find /root/.ssh -type f -name "*.pub" -exec chmod 644 {} \; 2>/dev/null || true
    
    # Config and known_hosts should be 644
    chmod 644 /root/.ssh/known_hosts* 2>/dev/null || true
    chmod 644 /root/.ssh/config 2>/dev/null || true
    
    echo "SSH keys configured successfully"
else
    echo "=========================================="
    echo "WARNING: No SSH keys found!"
    echo "=========================================="
    echo "SSH keys were not mounted to the container."
    echo ""
    echo "To mount SSH keys, run the container with:"
    echo "docker run -v <local_ssh_path>:/tmp/host_ssh:ro -it <image_name>"
    echo ""
fi

# Execute the main command
exec "$@"