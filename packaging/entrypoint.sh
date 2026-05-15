#!/bin/sh
if [ -z "$(ls -A /miniagents/resources 2>/dev/null)" ]; then
  echo "Initializing resources from defaults..."
  cp -r /miniagents/resources.default/. /miniagents/resources/
fi
exec "$@"
