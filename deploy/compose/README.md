# 팀 compose 운영 절차

`docker-compose.team.yml`은 Backend·Frontend·Kafka·MES Mock만 소유한다. PostgreSQL·
Neo4j·n8n은 공용 외부 서비스이며 이 compose로 생성·중지·삭제하지 않는다.

모든 명령은 project identity와 입력 파일을 동일하게 고정한다.

```bash
python deploy/compose/preflight_team_env.py --env-file deploy/compose/.env.team
docker compose -p bistel-team -f deploy/compose/docker-compose.team.yml \
  --env-file deploy/compose/.env.team config --quiet
docker compose -p bistel-team -f deploy/compose/docker-compose.team.yml \
  --env-file deploy/compose/.env.team up -d --build backend frontend kafka
docker compose -p bistel-team -f deploy/compose/docker-compose.team.yml \
  --env-file deploy/compose/.env.team exec -T kafka \
  /opt/team/manage_topics.sh ensure-topics kafka:9092
```

`KAFKA_AUTO_CREATE_TOPICS_ENABLE=false`이므로 마지막 `ensure-topics`는 선택 명령이 아니라
**필수 초기화 Gate**다. kafka가 healthy가 된 뒤 실행하고, 두 topic의 describe가 모두
성공하기 전에는 `mes-mock` profile을 시작하거나 공용 n8n WF3·WF4에 Kafka credential을
연결하지 않는다. 명령은 `--if-not-exists`라 재실행 가능하며 두 번째 실행 결과도 같은
partition=1·replication=1이어야 한다. compose service를 Backend·Frontend·Kafka·MES Mock
4종으로 유지하기 위해 별도 init service 대신 이 명시적 운영 단계를 정본으로 둔다.

`mes-mock`은 `--profile mes up -d mes-mock`으로 활성화한다. 이 consumer의 group은
`kosa-fdc-mes-mock`이고, n8n WF4 write-back group은 `kosa-fdc-wf4-writeback`이다. 두
group의 offset을 서로 대신 사용하지 않는다. compose는 Kafka client 사용자명·비밀번호
원문을 container environment에 넣지 않고 `/run/secrets/kafka_client_user`·
`/run/secrets/kafka_client_password`로 마운트하며, entrypoint는 `KAFKA_CLIENT_USER_FILE`·
`KAFKA_CLIENT_PASSWORD_FILE` 경로만 소비하고 원문 env fallback을 두지 않는다.

일반 `config` 출력에는 치환된 secret이 포함될 수 있으므로 저장·공유하지 않는다. 검증은
`config --quiet`, 서비스 확인은 `config --services`만 사용한다.

## Kafka network trust boundary

INTERNAL·EXTERNAL listener는 모두 `SASL_PLAINTEXT/PLAIN`이다. 이는 **인증만 제공하고
전송 구간 암호화나 credential 기밀성을 제공하지 않는다**. 현재 선택은 Kafka `53004`와 공용
n8n이 같은 학원 내부 신뢰망에 있고, host firewall이 `53004` 접근을 승인된 n8n/팀 host로만
제한한다는 배포 전제에서만 허용한다. 인터넷·비신뢰 VLAN·포트 포워딩에 노출해야 하면 이
compose를 적용하지 않고 TLS listener와 인증서 배포가 준비될 때까지 Kafka Gate를
`BLOCKED`로 둔다. `probe_external_kafka.py`의 잘못된 credential 음성은 인증 활성만
증명하며 채널 암호화를 증명하지 않는다.

## 적용 전후 증적

1. 적용 전 `docker ps --no-trunc`와 `docker network inspect`로 전체 container ID·image·
   publish port·network membership를 기록한다.
2. Frontend 53080·Kafka 53004가 기존 컨테이너에 사용되지 않는지 확인한다. 기존 공용
   PostgreSQL·Neo4j·n8n 포트와 컨테이너는 중지하지 않는다.
3. 기동 후 Backend liveness는 Frontend 경유 `GET http://<host>:53080/api/health`로 확인한다.
4. Backend 컨테이너에서 기존 verifier를 실행한다.

   ```bash
   docker compose -p bistel-team -f deploy/compose/docker-compose.team.yml \
     --env-file deploy/compose/.env.team exec -T backend \
     python scripts/prefetch_embedding_model.py --verify-only
   ```

5. Kafka topic은 위 one-shot을 두 번 실행하고 두 번째에도 두 topic의 partition=1·
   replication=1이 유지되는지 확인한다. 이어 아래 명령으로 별도 Docker network의
   `apache/kafka:3.9.1` 임시 컨테이너에서 advertised 주소 양성·topic 2개·잘못된
   credential 음성을 확인한다. 출력에는 주소와 secret을 남기지 않는다.

   ```bash
   python deploy/compose/probe_external_kafka.py \
     --env-file deploy/compose/.env.team
   ```

   공용 n8n 컨테이너에서도 DNS resolve와 TCP 접속을 확인한 뒤 C-4.5에 EXTERNAL
   주소·PLAIN credential object를 인계한다.
6. 적용 후 1번과 같은 목록을 다시 기록해 `bistel-team` 이외 container·network가
   불변인지 대조한다. detection joblib이 없으면 `anomaly signal unavailable`을 비차단
   상태로 기록한다.

Frontend build는 `/api`와 모든 production mock=false를 Docker build arg로 고정한다.
완성 image의 `/usr/share/nginx/html/assets`에 `localhost:8000`이 없고 `/api`가 있는지
검사한다.

## C-4.5 격리·공용 왕복

저장소 CI는 `backend/tests/fixtures/v5_c_4_5/docker-compose.yml`의 별도 project와
host port 39092로 Kafka 3.9.1을 기동한다. test credential만 사용하고 공용 Kafka에는
접속하지 않는다. `fdc.actions`를 처리한 MES Mock은 result broker ack가 확인된 뒤에만
input offset을 동기 commit한다.

첫 공용 왕복은 `kosa_agent_e2e`에만 기록한다. 아래 override는 Backend 한 개만 host
53081에 공개하며 production DB 이름을 코드로 받을 수 없게 고정한다.

```bash
docker compose -p bistel-team-e2e \
  -f deploy/compose/docker-compose.team.yml \
  -f deploy/compose/docker-compose.e2e-backend.yml \
  --env-file deploy/compose/.env.team config --quiet
docker compose -p bistel-team-e2e \
  -f deploy/compose/docker-compose.team.yml \
  -f deploy/compose/docker-compose.e2e-backend.yml \
  --env-file deploy/compose/.env.team up -d --build backend
```

실행 전 `kosa_agent`의 C 소유 테이블별 count와 정렬된 PK 기반 hash를 기록하고, 실행 뒤
같은 질의 결과가 불변인지 대조한다. n8n `BACKEND_BASE_URL` 기존 값과 WF3·WF4 활성 상태를
secret 없이 기록한 다음 임시로 `http://<host>:53081`을 사용한다. 승인된 EQP_HOLD 한 건이
`WAITING→SENDING→SENT`이고 `ACTION_SENT` 감사 한 건인지 확인한 뒤 URL과 workflow 상태를
원복하고 `bistel-team-e2e`만 `down`한다. production count/hash가 달라졌거나 원복을
증명하지 못하면 공용 Gate는 실패다.

## WF4 offset 관측과 제한 복구

malformed result는 payload 조회가 아니라 WF4 group의 topic/partition/offset 전후를
기록한다. offset이 정확히 1 증가하고 Backend callback·delivery·감사가 모두 0건이어야
discard 증적이다.

```bash
docker compose -p bistel-team -f deploy/compose/docker-compose.team.yml \
  --env-file deploy/compose/.env.team exec -T kafka \
  /opt/team/manage_wf4_offsets.sh describe kafka:9092
```

callback 실패는 먼저 Backend를 복구해 같은 record가 재시도되고 callback 2xx·offset
진행·lag 0으로 수렴하는지 확인한다. offset reset은 오진행이 확인된 경우에만 쓴다.
WF4를 비활성화하고 group 상태가 Empty/Dead인지 확인한 뒤 retention 범위 안의 정확한
partition/offset으로 dry-run하고, 출력 대조 후에만 확인 토큰을 붙여 실행한다.

```bash
docker compose -p bistel-team -f deploy/compose/docker-compose.team.yml \
  --env-file deploy/compose/.env.team exec -T kafka \
  /opt/team/manage_wf4_offsets.sh dry-run kafka:9092 <partition> <offset>
docker compose -p bistel-team -f deploy/compose/docker-compose.team.yml \
  --env-file deploy/compose/.env.team exec -T kafka \
  /opt/team/manage_wf4_offsets.sh execute kafka:9092 <partition> <offset> WF4_DISABLED
```

전체 topic reset, retention 밖 offset, 실행 중인 group reset은 helper가 거부한다. 실행 후
WF4를 재활성화하고 대상 delivery terminal 및 lag 0을 다시 확인한다. 명령 출력과 증적에는
credential·webhook secret·payload 원문을 남기지 않는다.

## rollback

```bash
docker compose -p bistel-team -f deploy/compose/docker-compose.team.yml \
  --env-file deploy/compose/.env.team down
```

`-v`를 붙이지 않는다. Kafka named volume과 다른 project의 container·network를 보존한다.
