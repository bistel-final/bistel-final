#!/bin/sh
set -eu

read_secret() {
  secret_file="$1"
  if [ ! -r "$secret_file" ]; then
    printf '%s\n' 'kafka secret file is not readable' >&2
    exit 78
  fi
  IFS= read -r secret_value < "$secret_file" || true
  if [ -z "$secret_value" ]; then
    printf '%s\n' 'kafka secret is empty' >&2
    exit 78
  fi
  printf '%s' "$secret_value"
}

broker_user="$(read_secret /run/secrets/kafka_broker_user)"
broker_password="$(read_secret /run/secrets/kafka_broker_password)"
client_user="$(read_secret /run/secrets/kafka_client_user)"
client_password="$(read_secret /run/secrets/kafka_client_password)"

umask 077
{
  printf '%s\n' 'KafkaServer {'
  printf '%s\n' '  org.apache.kafka.common.security.plain.PlainLoginModule required'
  printf '  username="%s"\n' "$broker_user"
  printf '  password="%s"\n' "$broker_password"
  printf '  user_%s="%s"\n' "$broker_user" "$broker_password"
  printf '  user_%s="%s";\n' "$client_user" "$client_password"
  printf '%s\n' '};'
} > /tmp/kafka_server_jaas.conf

unset broker_user broker_password client_user client_password secret_value
exec /etc/kafka/docker/run
