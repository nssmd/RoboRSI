#!/usr/bin/env bash
# Install udev rules for RoboRSI serial/camera device access.
# Run once after installation — requires sudo.
set -e

RULE_FILE="/etc/udev/rules.d/99-roborsi.rules"

cat > /tmp/99-roborsi.rules << 'EOF'
# RoboRSI: serial devices (CH340/CH341 USB-serial used by SO101 arms)
SUBSYSTEM=="tty", ATTRS{idVendor}=="1a86", MODE="0666"
# RoboRSI: Feetech servo USB-serial
SUBSYSTEM=="tty", ATTRS{idVendor}=="0483", MODE="0666"
# RoboRSI: generic USB-serial fallback
KERNEL=="ttyACM[0-9]*", MODE="0666"
KERNEL=="ttyUSB[0-9]*", MODE="0666"
# RoboRSI: video devices (cameras)
SUBSYSTEM=="video4linux", MODE="0666"
EOF

sudo cp /tmp/99-roborsi.rules "$RULE_FILE"
sudo udevadm control --reload-rules
sudo udevadm trigger
rm /tmp/99-roborsi.rules

echo "udev rules installed at $RULE_FILE"
echo "Serial and camera devices are now accessible without sudo."
