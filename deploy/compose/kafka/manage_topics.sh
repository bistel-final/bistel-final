#!/bin/sh
set -eu

read_secret() {
  secret_file="$1"
  if [ ! -r "$secret_file" ]; then
    printf '%s\n' 'kafka client secret file is not readable' >&2
    exit 78
  fi
  IFS= read -r secret_value < "$secret_file" || true
  if [ -z "$secret_value" ]; then
    printf '%s\n' 'kafka client secret is empty' >&2
    exit 78
  fi
  printf '%s' "$secret_value"
}

client_user="$(read_secret /run/secrets/kafka_client_user)"
client_password="$(read_secret /run/secrets/kafka_client_password)"

properties="$(mktemp)"
trap 'rm -f "$properties"' EXIT HUP INT TERM
umask 077
{
  printf '%s\n' 'security.protocol=SASL_PLAINTEXT'
  printf '%s\n' 'sasl.mechanism=PLAIN'
  printf 'sasl.jaas.config=org.apache.kafka.common.security.plain.PlainLoginModule required username="%s" password="%s";\n' \
    "$client_user" "$client_password"
} > "$properties"
unset client_user client_password secret_value

command="${1:-}"
bootstrap="${2:-kafka:9092}"

case "$command" in
  health)
    exec /opt/kafka/bin/kafka-broker-api-versions.sh \
      --bootstrap-server "$bootstrap" \
      --command-config "$properties" >/dev/null
    ;;
  ensure-topics)
    for topic in fdc.actions fdc.actions.result; do
      /opt/kafka/bin/kafka-topics.sh \
        --bootstrap-server "$bootstrap" \
        --command-config "$properties" \
        --create --if-not-exists \
        --topic "$topic" --partitions 1 --replication-factor 1 >/dev/null
      /opt/kafka/bin/kafka-topics.sh \
        --bootstrap-server "$bootstrap" \
        --command-config "$properties" \
        --describe --topic "$topic"
    done
    ;;
  list-topics)
    exec /opt/kafka/bin/kafka-topics.sh \
      --bootstrap-server "$bootstrap" \
      --command-config "$properties" --list
    ;;
  *)
    printf '%s\n' 'usage: manage_topics.sh {health|ensure-topics|list-topics} [bootstrap]' >&2
    exit 64
    ;;
esac
