#!/bin/sh
set -eu

group='kosa-fdc-wf4-writeback'
topic='fdc.actions.result'

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
partition="${3:-}"
offset="${4:-}"
confirmation="${5:-}"

consumer_groups() {
  /opt/kafka/bin/kafka-consumer-groups.sh \
    --bootstrap-server "$bootstrap" --command-config "$properties" "$@"
}

case "$command" in
  describe)
    exec /opt/kafka/bin/kafka-consumer-groups.sh \
      --bootstrap-server "$bootstrap" --command-config "$properties" \
      --describe --group "$group"
    ;;
  dry-run|execute)
    case "$partition" in
      ''|*[!0-9]*)
        printf '%s\n' 'partition and offset must be non-negative integers' >&2
        exit 64
        ;;
    esac
    case "$offset" in
      ''|*[!0-9]*)
        printf '%s\n' 'partition and offset must be non-negative integers' >&2
        exit 64
        ;;
    esac

    earliest="$(/opt/kafka/bin/kafka-get-offsets.sh \
      --bootstrap-server "$bootstrap" --command-config "$properties" \
      --topic "$topic:$partition" --time -2 | awk -F: 'NR == 1 {print $3}')"
    latest="$(/opt/kafka/bin/kafka-get-offsets.sh \
      --bootstrap-server "$bootstrap" --command-config "$properties" \
      --topic "$topic:$partition" --time -1 | awk -F: 'NR == 1 {print $3}')"
    case "$earliest" in
      ''|*[!0-9]*)
        printf '%s\n' 'retention bounds unavailable' >&2
        exit 69
        ;;
    esac
    case "$latest" in
      ''|*[!0-9]*)
        printf '%s\n' 'retention bounds unavailable' >&2
        exit 69
        ;;
    esac
    if [ "$offset" -lt "$earliest" ] || [ "$offset" -gt "$latest" ]; then
      printf '%s\n' 'requested offset is outside broker retention bounds' >&2
      exit 65
    fi

    mode='--dry-run'
    if [ "$command" = 'execute' ]; then
      if [ "$confirmation" != 'WF4_DISABLED' ]; then
        printf '%s\n' 'execute requires the WF4_DISABLED confirmation token' >&2
        exit 64
      fi
      state="$(consumer_groups --describe --group "$group" --state 2>/dev/null || true)"
      if ! printf '%s\n' "$state" | grep -Eq '(^|[[:space:]])(Empty|Dead)($|[[:space:]])'; then
        printf '%s\n' 'consumer group must be Empty or Dead before reset' >&2
        exit 75
      fi
      mode='--execute'
    fi

    consumer_groups --reset-offsets --group "$group" \
      --topic "$topic:$partition" --to-offset "$offset" "$mode"
    ;;
  *)
    printf '%s\n' 'usage: manage_wf4_offsets.sh {describe|dry-run|execute} [bootstrap] [partition] [offset] [WF4_DISABLED]' >&2
    exit 64
    ;;
esac
