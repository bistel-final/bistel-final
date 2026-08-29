# Delivery UNKNOWN·retry 운영 절차

`V5-C-4.6`의 수동 복구와 public 멱등 증적 절차다. 자동 UNKNOWN 확정·자동 retry·자동
재발송은 없다. 모든 DB 변경은 `backend/scripts/manage_delivery_recovery.py`만 사용한다.

## 1. 안전 경계

- 허용 DB는 `kosa_agent_e2e`, `kosa_agent`뿐이다. 최초 rehearsal은 반드시
  `kosa_agent_e2e`에서 수행한다.
- CLI가 연결 후 `current_database()`를 다시 확인한다. `--target`과 다르면 변경 0이다.
- 콘솔에는 `action_id`, `channel`, 상태, reason code만 남는다. DSN·수신자·payload·secret을
  복사하거나 증적에 넣지 않는다.
- `SENDING`이 cutoff를 지났다는 사실만으로 UNKNOWN을 확정하지 않는다. 먼저 SMTP 수신함
  또는 Kafka input/result offset과 provider 상태를 사람이 확인한다.
- `UNKNOWN`은 retry 대상이 아니다. 실제 외부 효과를 확인해 별도 운영 판단을 내리며 자동
  재발행하지 않는다.

## 2. stale 조회와 UNKNOWN 확정

Backend image 내부 또는 같은 Python 환경에서 실행한다.

```bash
python backend/scripts/manage_delivery_recovery.py \
  --target kosa_agent_e2e list-stale
```

EMAIL이면 승인된 수신함에서 해당 조치가 실제 발송됐는지 확인한다. MES_MOCK이면
`fdc.actions`와 `fdc.actions.result`의 대상 partition offset, result key, WF4 lag를 확인한다.
확인 완료 후 아래의 네 값을 실제 조치 키로 바꾸고 confirmation 문자열을 그대로 다시 입력한다.

```bash
python backend/scripts/manage_delivery_recovery.py \
  --target kosa_agent_e2e confirm-unknown \
  --action-id ACT-REPLACE \
  --channel EMAIL \
  --provider-checked \
  --confirm "confirm-unknown kosa_agent_e2e ACT-REPLACE EMAIL"
```

`APPLIED`만 변경 성공이다. `STILL_FRESH`, `CALLBACK_WON`, `STATE_NOT_ALLOWED`, `NO_TARGET`,
`TARGET_DB_MISMATCH`, `CONFIRMATION_MISMATCH`는 exit 3·변경 0이다. callback이 먼저 terminal을
확정하면 `CALLBACK_WON`, UNKNOWN이 먼저 확정되면 늦은 callback은 409가 정상이다.

## 3. FAILED 명시 retry

실패 원인을 제거하고 동일 `action_id`, `channel`, `request_hash`로 재시도해도 되는지 확인한다.
MES_MOCK은 기존 approval과 동일 run/thread를 복구한 경우에만 이후 claim이 승인 결속을 다시
검증한다.

```bash
python backend/scripts/manage_delivery_recovery.py \
  --target kosa_agent_e2e retry \
  --action-id ACT-REPLACE \
  --channel EMAIL \
  --confirm "retry kosa_agent_e2e ACT-REPLACE EMAIL"
```

이 명령은 `FAILED→WAITING`만 수행한다. attempt는 증가하지 않고 이전 오류와 request hash를
보존한다. 다음 `begin_*_delivery` claim이 attempt를 증가시키며, timer·startup·catch-up에서
이 retry 함수를 호출하는 경로는 없다.

## 4. public callback trail 준비·보존

평상시 `DELIVERY_CALLBACK_TRAIL_DIR`와 `DELIVERY_CALLBACK_TRAIL_RUN_ID`는 모두 비운다.
controlled run 직전에 host directory를 만들고 둘을 함께 설정한다.

```bash
install -d -m 0700 deploy/compose/trail
export DELIVERY_CALLBACK_TRAIL_DIR=/var/lib/bistel/delivery-trail
export DELIVERY_CALLBACK_TRAIL_RUN_ID=c46_RUN_REPLACE
docker compose -p bistel-team-e2e \
  -f deploy/compose/docker-compose.team.yml \
  -f deploy/compose/docker-compose.e2e-backend.yml \
  --env-file deploy/compose/.env.team up -d --build backend
```

run ID는 영숫자·`_`·`-` 1~64자만 허용한다. host directory는 host 운영자 소유·0700,
파일은 컨테이너 실행 UID 소유·0600이며 symlink와 기존 `trail-<run_id>.jsonl` 재사용을 기동
단계에서 거부한다. Backend가 root로 실행되는 현재 image는 0700 host bind에 쓸 수 있지만,
다른 non-root UID로 실행한다면 directory 소유자를 그 UID와 맞춰야 한다.
trail에는 exact 6필드(`ts`, `action_id`, `channel`, `status`, `duplicate`, `http_status`)만
기록한다. write 실패가 callback 업무 응답을 뒤집지는 않지만 verifier는 누락을 `BLOCKED`로
판정한다.

controlled run 뒤 env 두 개를 해제하고 Backend를 재생성한다. `./trail` host bind는 재생성
뒤에도 파일을 보존한다. verifier 판정과 증적 첨부 전에는 삭제하지 않는다.

```bash
unset DELIVERY_CALLBACK_TRAIL_DIR DELIVERY_CALLBACK_TRAIL_RUN_ID
docker compose -p bistel-team-e2e \
  -f deploy/compose/docker-compose.team.yml \
  -f deploy/compose/docker-compose.e2e-backend.yml \
  --env-file deploy/compose/.env.team up -d --force-recreate backend
```

## 5. Kafka·WF4 증적과 verifier

MES Mock의 observable effect는 별도 시스템 변경이 아니라 `fdc.actions.result` 발행 1건이다.
controlled run 전후 `fdc.actions`·`fdc.actions.result` partition end offset을 기록하고,
result record는 value를 출력하지 않은 채 key만 읽어 대상 `action_id`와 대조한다. WF4 group은
아래 명령으로 전후 lag를 확인한다.

```bash
docker compose -p bistel-team -f deploy/compose/docker-compose.team.yml \
  --env-file deploy/compose/.env.team exec -T kafka \
  /opt/team/manage_wf4_offsets.sh describe kafka:9092
```

EMAIL artifact는 승인된 수신함의 실제 수신 통수 1을 기록한다. MES artifact는 input/result
offset before/after, result key, WF4 lag before/after를 기록한다. artifact JSON은
`delivery-artifact-v1` exact 필드 계약을 따르며 secret·DSN·메일주소·payload 원문을 포함하지
않는다.

아래 MES 템플릿에서 식별자와 실제 관측값만 바꾼다. EMAIL은
`smtp_received_count`를 `1`로 두고 offset·result·lag 7개 필드를 모두 `null`로 둔다.

```json
{
  "schema": "delivery-artifact-v1",
  "run_id": "c46_RUN_REPLACE",
  "action_id": "ACT-REPLACE",
  "channel": "MES_MOCK",
  "injected_count": 1,
  "expected_first_count": 1,
  "expected_duplicate_count": 1,
  "expected_conflict_count": 1,
  "smtp_received_count": null,
  "actions_offset_before": 100,
  "actions_offset_after": 101,
  "result_offset_before": 200,
  "result_offset_after": 201,
  "result_key": "ACT-REPLACE",
  "wf4_lag_before": 0,
  "wf4_lag_after": 0
}
```

artifact JSON을 `deploy/compose/trail/delivery-artifact.json`에 0600으로 둔 뒤, bind mount를
통해 Backend 컨테이너 안에서 verifier를 실행한다. 이렇게 하면 Linux host에서 root 소유로
생성된 0600 trail도 권한을 완화하지 않고 읽을 수 있다.

```bash
docker compose -p bistel-team-e2e \
  -f deploy/compose/docker-compose.team.yml \
  -f deploy/compose/docker-compose.e2e-backend.yml \
  --env-file deploy/compose/.env.team exec -T backend \
  python scripts/verify_delivery_artifact.py \
  --artifact /var/lib/bistel/delivery-trail/delivery-artifact.json \
  --trail /var/lib/bistel/delivery-trail/trail-c46_RUN_REPLACE.jsonl
```

필드·파일 누락, 허용하지 않은 필드, first/duplicate/409 수 불일치, EMAIL 수신 1통 불일치,
MES input delta 불일치, result delta≠1, result key 불일치, WF4 lag after≠0은 모두 exit 3
`BLOCKED`다. 모든 증적이 `PASSED`인 경우에만 WF2·WF3·WF4 영구 활성로 넘어간다.

## 6. 영구 활성과 rollback

1. 적용 직전 WF4 lag를 확인하고 0이 아니거나 조회 불가면 활성화하지 않는다.
2. WF2·WF3·WF4 runtime manifest hash가 저장소 정본과 같은지 확인한다.
3. controlled run artifact가 `PASSED`인지 확인한다.
4. workflow를 활성화한 직후 WF4 lag를 다시 확인한다.
5. 불일치·증적 누락·lag 비수렴이면 workflow를 다시 비활성화하고 Backend trail env를 제거한다.
6. Kafka offset reset은 `deploy/compose/README.md`의 제한 복구 절차만 사용한다. topic 전체
   reset이나 실행 중인 group reset은 금지한다.

public 확인 전 상태는 `repository PROVEN / public BLOCKED`다.
