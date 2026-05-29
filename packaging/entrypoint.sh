#!/bin/sh
if [ -z "$(ls -A /ipmaster-cowork/resources 2>/dev/null)" ]; then
  echo "Initializing resources from defaults..."
  cp -r /ipmaster-cowork/resources.default/. /ipmaster-cowork/resources/
fi
exec "$@"
