// 자연어 분석 fixture — 명세 AnalysisQueryResponse 스키마
// rows 는 객체 배열이다 (배열의 배열 아님). columns·latency_ms 필드명도 명세 기준.
// 예시 질문은 아래 NL_CHIPS 참조. (mock 응답 매핑은 구 칩 기준이라 실서버 모드에서만 칩이 온전히 동작한다)

// 예시 질문 — 엔지니어가 실제로 묻는 네 가지. 능력 한 칸씩(숫자·비교·추이·구조)을 증명하고,
// 순서대로 누르면 하나의 조사 과정(랏 → 파라미터 → 챔버 추이 → 설비 구조)이 된다.
// 마지막 구조 질문은 Neo4j 교차확인을 실제로 발동시켜 파이프라인 5단계가 ✓ 가 되게 한다.
// 방어 시연("전부 지워줘")은 칩에 두지 않고 발표 중 직접 입력한다. 상대 날짜(오늘·이번 주)는 쓰지 않는다 — 데이터가 8/2~8/11 고정.
export const NL_CHIPS = [
  { kind: '숫자', q: 'LOT004에서 OOS 알람이 발생한 웨이퍼는 몇 장이야?' },
  { kind: '비교', q: '어떤 파라미터에서 OOS 알람이 가장 많이 발생했어?' },
  { kind: '추이', q: '일자별 OOS 알람 발생 추이를 보여줘' },
  { kind: '구조', q: 'EQP04 설비에는 챔버가 몇 개 있어?' },
]

export const NL_QUERIES = {
  "전체 알람이 몇 건이야?": {
    "question": "전체 알람이 몇 건이야?",
    "sql": "SELECT COUNT(*) AS alarm_cnt\nFROM fdc_alarm\nLIMIT 500",
    "columns": [
      "alarm_cnt"
    ],
    "rows": [
      {
        "alarm_cnt": 51
      }
    ],
    "row_count": 1,
    "metric": {
      "type": "count",
      "column": null,
      "p": null
    },
    "metric_result": 51,
    "group_by": [],
    "visualization": {
      "chart_type": "table",
      "x": null,
      "y": "alarm_cnt"
    },
    "latency_ms": 1240
  },
  "챔버별 알람 건수 내림차순": {
    "question": "챔버별 알람 건수 내림차순",
    "sql": "SELECT chamber_id, COUNT(*) AS cnt\nFROM fdc_alarm\nGROUP BY chamber_id\nORDER BY cnt DESC\nLIMIT 500",
    "columns": [
      "chamber_id",
      "cnt"
    ],
    "rows": [
      {
        "chamber_id": "PHO-01-C1",
        "cnt": 22
      },
      {
        "chamber_id": "ETC-01-C2",
        "cnt": 15
      },
      {
        "chamber_id": "ETC-01-C1",
        "cnt": 14
      }
    ],
    "row_count": 3,
    "metric": {
      "type": "count",
      "column": null,
      "p": null
    },
    "metric_result": [
      {
        "group": {
          "chamber_id": "PHO-01-C1"
        },
        "value": 22
      },
      {
        "group": {
          "chamber_id": "ETC-01-C2"
        },
        "value": 15
      },
      {
        "group": {
          "chamber_id": "ETC-01-C1"
        },
        "value": 14
      }
    ],
    "group_by": [
      "chamber_id"
    ],
    "visualization": {
      "chart_type": "bar",
      "x": "chamber_id",
      "y": "cnt"
    },
    "latency_ms": 940
  },
  "센서별 OOS 알람 수": {
    "question": "센서별 OOS 알람 수",
    "sql": "SELECT sensor_id, COUNT(*) AS cnt\nFROM fdc_alarm\nWHERE judgement = 'OOS'\nGROUP BY sensor_id\nORDER BY cnt DESC\nLIMIT 500",
    "columns": [
      "sensor_id",
      "cnt"
    ],
    "rows": [
      {
        "sensor_id": "PH_FOCUS",
        "cnt": 17
      },
      {
        "sensor_id": "ET_REFL",
        "cnt": 11
      },
      {
        "sensor_id": "ET_CF4",
        "cnt": 9
      }
    ],
    "row_count": 3,
    "metric": {
      "type": "count",
      "column": null,
      "p": null
    },
    "metric_result": [
      {
        "group": {
          "sensor_id": "PH_FOCUS"
        },
        "value": 17
      },
      {
        "group": {
          "sensor_id": "ET_REFL"
        },
        "value": 11
      },
      {
        "group": {
          "sensor_id": "ET_CF4"
        },
        "value": 9
      }
    ],
    "group_by": [
      "sensor_id"
    ],
    "visualization": {
      "chart_type": "bar",
      "x": "sensor_id",
      "y": "cnt"
    },
    "latency_ms": 870
  },
  "규칙별 알람 분포": {
    "question": "규칙별 알람 분포",
    "sql": "SELECT rule_id, COUNT(*) AS cnt\nFROM fdc_alarm\nGROUP BY rule_id\nORDER BY cnt DESC\nLIMIT 500",
    "columns": [
      "rule_id",
      "cnt"
    ],
    "rows": [
      {
        "rule_id": "R01_OOS",
        "cnt": 34
      },
      {
        "rule_id": "R02_OOC",
        "cnt": 14
      },
      {
        "rule_id": "R03_CONSEC",
        "cnt": 3
      }
    ],
    "row_count": 3,
    "metric": {
      "type": "count",
      "column": null,
      "p": null
    },
    "metric_result": [
      {
        "group": {
          "rule_id": "R01_OOS"
        },
        "value": 34
      },
      {
        "group": {
          "rule_id": "R02_OOC"
        },
        "value": 14
      },
      {
        "group": {
          "rule_id": "R03_CONSEC"
        },
        "value": 3
      }
    ],
    "group_by": [
      "rule_id"
    ],
    "visualization": {
      "chart_type": "bar",
      "x": "rule_id",
      "y": "cnt"
    },
    "latency_ms": 1655
  }
}

export const NL_REJECTS = {
  "알람 테이블 전부 지워줘": {
    "question": "알람 테이블 전부 지워줘",
    "rejected": true,
    "reason": "POLICY_REJECTED: SELECT 외 구문 거부",
    "latency_ms": 310
  },
  "document_chunk 보여줘": {
    "question": "document_chunk 보여줘",
    "rejected": true,
    "reason": "POLICY_REJECTED: 허용 목록 밖 테이블",
    "latency_ms": 120
  }
}

// 최근 질의 초기 이력 (디자인 v2 시안 5건: 성공 3 · 거부 2)
export const NL_INITIAL_HISTORY = [
  { question: '챔버별 알람 건수 내림차순', ok: true, row_count: 3, latency_ms: 940, reason: null },
  { question: '전체 알람이 몇 건이야?', ok: true, row_count: 1, latency_ms: 1240, reason: null },
  { question: '알람 테이블 전부 지워줘', ok: false, row_count: 0, latency_ms: 310, reason: 'POLICY_REJECTED: SELECT 외 구문 거부' },
  { question: '센서별 OOS 알람 수', ok: true, row_count: 3, latency_ms: 870, reason: null },
  { question: 'document_chunk 보여줘', ok: false, row_count: 0, latency_ms: 120, reason: 'POLICY_REJECTED: 허용 목록 밖 테이블' },
]
