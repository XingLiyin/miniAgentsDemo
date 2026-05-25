#!/bin/sh
if [ -z "$(ls -A /netlive-cowork/resources 2>/dev/null)" ]; then
  echo "Initializing resources from defaults..."
  cp -r /netlive-cowork/resources.default/. /netlive-cowork/resources/
fi
exec "$@"
