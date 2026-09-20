#!/bin/sh
set -eu

SCRIPT_DIRECTORY=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_DIRECTORY=$(dirname "$SCRIPT_DIRECTORY")
BUILD_DIRECTORY="$PROJECT_DIRECTORY/.build/app"
APP_DIRECTORY="$BUILD_DIRECTORY/OpenStream100.app"
CONTENTS_DIRECTORY="$APP_DIRECTORY/Contents"

cd "$PROJECT_DIRECTORY"
mkdir -p ".build/module-cache"
export CLANG_MODULE_CACHE_PATH="${CLANG_MODULE_CACHE_PATH:-$PROJECT_DIRECTORY/.build/module-cache}"
export SWIFTPM_MODULECACHE_OVERRIDE="${SWIFTPM_MODULECACHE_OVERRIDE:-$PROJECT_DIRECTORY/.build/module-cache}"
swift build -c release --disable-sandbox -debug-info-format none

mkdir -p "$CONTENTS_DIRECTORY/MacOS" "$CONTENTS_DIRECTORY/Resources"
cp ".build/release/OpenStream100Mac" "$CONTENTS_DIRECTORY/MacOS/OpenStream100"
cp "Resources/Info.plist" "$CONTENTS_DIRECTORY/Info.plist"

codesign --force --deep --sign - "$APP_DIRECTORY"
echo "$APP_DIRECTORY"
