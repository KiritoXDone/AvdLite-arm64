#!/system/bin/sh
set -eu

# KernelSU recognizes the manager from the data partition; check its location on each boot.
BUNDLED=/product/app/KernelSU/lib/arm64/libksud.so
if [ ! -x /data/adb/ksud ]; then
    "$BUNDLED" install
fi
case "$(pm path me.weishu.kernelsu)" in
    package:/data/app/*) ;;
    *) pm install -r /product/app/KernelSU/KernelSU.apk ;;
esac
/data/adb/ksud debug version
setprop sys.avdlite.ksu_ready 1
