# BISTel FDC Agent API 명세서

> 버전: 3.0
> machine canonical: `api_spec_v3.json`
> canonical JSON만으로 재생성하며 live OpenAPI로 빈 계약을 보충하지 않는다.

## 1. 공통 규칙

- JSON field는 snake_case를 사용하며 명시된 전환 alias만 예외로 한다.
- 요청·응답 DTO는 알 수 없는 field를 거부한다.
- 날짜는 YYYY-MM-DD, 시각은 UTC offset을 포함한 ISO 8601 date-time을 사용한다.
- 빈 목록은 200과 []로 반환한다.
- 문자열 ID는 trim 후 빈 문자열을 거부한다.
- 합성 Fault 정답은 Runtime·Agent·일반 조회 API에서 반환하지 않는다.
- 목록은 명세의 보조키까지 사용해 안정 정렬한다.

## 2. 오류 응답

| HTTP | 의미 |
|---:|---|
| 401 | internal endpoint 인증·secret 검증 실패 |
| 404 | 식별자로 요청한 리소스 없음 |
| 409 | 멱등성 또는 상태 전이 충돌 |
| 422 | 요청 형식·Enum·범위 오류 |
| 500 | 예상하지 못한 서버 오류 |
| 503 | 외부 의존성 준비 실패 |

## 3. API inventory — 35개

| # | 구분 | 담당 | Method | Path | 요약 | 계약 |
|---:|---|---|---|---|---|---|
| 1 | 필수 | A | GET | `/alarms` | 저장·파생 알람 조회 | semantic |
| 2 | 필수 | A | GET | `/trace` | wafer parameter Trace | semantic |
| 3 | 필수 | A | GET | `/parameters` | parameter 5선 기준정보 | semantic |
| 4 | 필수 | B | POST | `/documents/search` | RAG 문서 검색 | semantic |
| 5 | 보안필수 | B | GET | `/relations/chambers/{chamber_id}` | Ontology 선택 chamber subgraph·context | semantic |
| 6 | 필수 | C | GET | `/agent/runs` | Agent 실행 이력 | semantic |
| 7 | 필수 | C | POST | `/agent/ask` | 근거 기반 Agent 질의 | semantic |
| 8 | 필수 | C | GET | `/approvals` | 승인 대기·결정 이력 | semantic |
| 9 | 필수 | C | POST | `/approvals/{approval_id}/decision` | 승인·반려 | semantic |
| 10 | 필수 | D | GET | `/audit-logs` | append-only 감사 이력 | semantic |
| 11 | 확장 | A | GET | `/dataset/bounds` | 데이터 epoch·필터 범위 | inventory |
| 12 | 확장 | A | GET | `/dashboard/summary` | 서버 대시보드 집계 | semantic |
| 13 | 확장 | A | GET | `/alarms/{source}/{alarm_id}` | source-aware 알람 상세 | semantic |
| 14 | 확장 | A | GET | `/alarms/paged` | 페이지 알람 목록 | semantic |
| 15 | 확장 | A | GET | `/traces/catalog` | Trace 선택 목록 | semantic |
| 16 | 확장 | A | POST | `/traces/search` | 복합 Trace 검색 | semantic |
| 17 | 확장 | B | GET | `/relations/equipment/{equipment_id}` | 설비 관계 | inventory |
| 18 | 확장 | B | GET | `/documents/{document_id}` | 문서 상세 | semantic |
| 19 | 실행필수 | C | POST | `/agent/runs` | Agent 분석 시작 | semantic |
| 20 | 확장 | C | GET | `/agent/runs/{run_id}` | Agent 실행 상세 | semantic |
| 21 | 확장 | C | POST | `/agent/runs/{run_id}/retry` | 실패 실행 재시도 | inventory |
| 22 | 확장 | C | GET | `/agent/runs/paged` | 페이지 실행 이력 | inventory |
| 23 | 확장 | C | GET | `/approvals/paged` | 페이지 승인 이력 | inventory |
| 24 | 확장 | C | GET | `/actions` | action 목록 | semantic |
| 25 | 확장 | C | GET | `/actions/{action_id}` | action·delivery 상세 | semantic |
| 26 | 확장 | C | POST | `/actions/{action_id}/deliveries/{channel}/retry` | 실패 channel 재전송 | inventory |
| 27 | 내부 | C | POST | `/internal/actions/{action_id}/delivery` | delivery 상태 write-back | semantic |
| 28 | 팀필수 | D | POST | `/analytics/query` | 자연어 Text2SQL | semantic |
| 29 | 확장 | D | POST | `/analytics/graph-query` | 자연어 Graph 질의 | semantic |
| 30 | 팀필수 | D | POST | `/analytics/validate` | SQL 실행 없는 검증 | semantic |
| 31 | 팀필수 | D | GET | `/analytics/history` | 질의 이력·재실행 원본 | semantic |
| 32 | 팀필수 | D | GET | `/analytics/evaluations` | Text2SQL 평가 이력 | semantic |
| 33 | 팀필수 | D | GET | `/audit-logs/paged` | 전역 페이지 감사 조회 | semantic |
| 34 | 운영 | Common | GET | `/health` | process liveness | semantic |
| 35 | 운영 | Common | GET | `/health/ready` | 통합 readiness | semantic |

## 4. Operation 상세

### 4.1 `GET /alarms`

- 구분/담당: 필수 / A
- 요청: query: date_from,date_to는 선택 pair; area,equipment,chamber,parameter,source,include_derived 선택; 빈 문자열→unset은 equipment,chamber,parameter만
- 성공 응답: AlarmItem[]
- 기타 상태: 422,503
- 정렬·제약: occurred_at DESC; source ASC; alarm_id DESC
- 호환·경계: 무파라미터 기본 TRACE138+SUMMARY51=189; include_derived=true면 192; source=R03이면 include_derived=false보다 우선해 3; identity=(source,alarm_id); canonical equipment_id,chamber_id,recipe_id,lot_id,wafer_id,parameter_id,recipe_step_no + deprecated 축약 alias
- 계약 규칙:
  - compatibility aliases derive only from canonical fields
  - source R03 requires alarm_type OOS, rule_code R03_CONSEC, and value null
  - source SUMMARY requires rule_code SUMMARY_OOC
  - source TRACE requires rule_code TRACE_OOS

```json
{
  "request": {
    "body": null,
    "header": {},
    "path": {},
    "query": {
      "area": {
        "nullable": true,
        "required": false,
        "schema": {
          "enum": [
            "ALL",
            "Etch",
            "Photo"
          ],
          "type": "string"
        }
      },
      "chamber": {
        "nullable": true,
        "required": false,
        "schema": {
          "type": "string"
        }
      },
      "date_from": {
        "nullable": true,
        "required": false,
        "schema": {
          "format": "date",
          "type": "string"
        }
      },
      "date_to": {
        "nullable": true,
        "required": false,
        "schema": {
          "format": "date",
          "type": "string"
        }
      },
      "equipment": {
        "nullable": true,
        "required": false,
        "schema": {
          "type": "string"
        }
      },
      "include_derived": {
        "nullable": false,
        "required": false,
        "schema": {
          "default": false,
          "type": "boolean"
        }
      },
      "parameter": {
        "nullable": true,
        "required": false,
        "schema": {
          "type": "string"
        }
      },
      "source": {
        "nullable": true,
        "required": false,
        "schema": {
          "enum": [
            "R03",
            "SUMMARY",
            "TRACE"
          ],
          "type": "string"
        }
      }
    }
  },
  "responses": {
    "200": {
      "schema": {
        "items": {
          "additional_properties": false,
          "fields": {
            "action_code": {
              "nullable": true,
              "required": false,
              "schema": {
                "enum": [
                  "EQP_HOLD",
                  "MONITORING",
                  "WARNING"
                ],
                "type": "string"
              }
            },
            "alarm_id": {
              "nullable": false,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            },
            "alarm_type": {
              "nullable": false,
              "required": true,
              "schema": {
                "enum": [
                  "OOC",
                  "OOS"
                ],
                "type": "string"
              }
            },
            "area": {
              "nullable": false,
              "required": true,
              "schema": {
                "enum": [
                  "Etch",
                  "Photo"
                ],
                "type": "string"
              }
            },
            "chamber": {
              "nullable": false,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            },
            "chamber_id": {
              "nullable": false,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            },
            "cl": {
              "nullable": true,
              "required": false,
              "schema": {
                "type": "number"
              }
            },
            "equipment": {
              "nullable": false,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            },
            "equipment_id": {
              "nullable": false,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            },
            "fault": {
              "nullable": true,
              "required": false,
              "schema": {
                "enum": [
                  "FOC",
                  "MFD",
                  "OTH",
                  "RFM",
                  "TMD"
                ],
                "type": "string"
              }
            },
            "lcl": {
              "nullable": true,
              "required": false,
              "schema": {
                "type": "number"
              }
            },
            "lot": {
              "nullable": false,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            },
            "lot_id": {
              "nullable": false,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            },
            "mes": {
              "nullable": false,
              "required": true,
              "schema": {
                "type": "string"
              }
            },
            "mes_status": {
              "nullable": true,
              "required": false,
              "schema": {
                "enum": [
                  "BLOCKED",
                  "CANCELED",
                  "FAILED",
                  "SENDING",
                  "SENT",
                  "UNKNOWN",
                  "WAITING"
                ],
                "type": "string"
              }
            },
            "notify": {
              "nullable": false,
              "required": true,
              "schema": {
                "type": "boolean"
              }
            },
            "notify_status": {
              "nullable": true,
              "required": false,
              "schema": {
                "enum": [
                  "FAILED",
                  "SENDING",
                  "SENT",
                  "UNKNOWN",
                  "WAITING"
                ],
                "type": "string"
              }
            },
            "occurred_at": {
              "nullable": false,
              "required": true,
              "schema": {
                "format": "date-time",
                "type": "string"
              }
            },
            "parameter": {
              "nullable": false,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            },
            "parameter_id": {
              "nullable": false,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            },
            "predicted_fault_code": {
              "nullable": true,
              "required": false,
              "schema": {
                "enum": [
                  "FOC",
                  "MFD",
                  "OTH",
                  "RFM",
                  "TMD"
                ],
                "type": "string"
              }
            },
            "recipe": {
              "nullable": false,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            },
            "recipe_id": {
              "nullable": false,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            },
            "recipe_step_no": {
              "nullable": false,
              "required": true,
              "schema": {
                "minimum": 1.0,
                "type": "integer"
              }
            },
            "rule_code": {
              "nullable": false,
              "required": true,
              "schema": {
                "enum": [
                  "R03_CONSEC",
                  "SUMMARY_OOC",
                  "TRACE_OOS"
                ],
                "type": "string"
              }
            },
            "seq_no": {
              "nullable": true,
              "required": false,
              "schema": {
                "minimum": 0.0,
                "type": "integer"
              }
            },
            "source": {
              "nullable": false,
              "required": true,
              "schema": {
                "enum": [
                  "R03",
                  "SUMMARY",
                  "TRACE"
                ],
                "type": "string"
              }
            },
            "statistic_type": {
              "nullable": true,
              "required": false,
              "schema": {
                "type": "string"
              }
            },
            "step_no": {
              "nullable": false,
              "required": true,
              "schema": {
                "minimum": 1.0,
                "type": "integer"
              }
            },
            "ucl": {
              "nullable": true,
              "required": false,
              "schema": {
                "type": "number"
              }
            },
            "value": {
              "nullable": true,
              "required": false,
              "schema": {
                "type": "number"
              }
            },
            "wafer": {
              "nullable": false,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            },
            "wafer_id": {
              "nullable": false,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            }
          },
          "type": "object"
        },
        "type": "array"
      },
      "shape": "array"
    },
    "422": {
      "schema": {
        "additional_properties": true,
        "fields": {
          "detail": {
            "nullable": false,
            "required": false,
            "schema": {
              "items": {
                "additional_properties": true,
                "fields": {
                  "loc": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "items": {
                        "type": "union",
                        "variants": [
                          {
                            "type": "string"
                          },
                          {
                            "type": "integer"
                          }
                        ]
                      },
                      "type": "array"
                    }
                  },
                  "msg": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "type": "string"
                    }
                  },
                  "type": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "type": "string"
                    }
                  }
                },
                "type": "object"
              },
              "type": "array"
            }
          }
        },
        "type": "object"
      },
      "shape": "object"
    }
  }
}
```

### 4.2 `GET /trace`

- 구분/담당: 필수 / A
- 요청: query: lot,wafer,chamber,parameter 필수
- 성공 응답: TracePoint[]
- 기타 상태: 422,503
- 정렬·제약: measured_at ASC; recipe_step_no ASC; seq_no ASC
- 호환·경계: 빈 결과=[]; seq_no는 step1 0..2와 step2 3..5
- 계약 규칙:
  - 없음

```json
{
  "request": {
    "body": null,
    "header": {},
    "path": {},
    "query": {
      "chamber": {
        "nullable": false,
        "required": true,
        "schema": {
          "min_length": 1,
          "type": "string"
        }
      },
      "lot": {
        "nullable": false,
        "required": true,
        "schema": {
          "min_length": 1,
          "type": "string"
        }
      },
      "parameter": {
        "nullable": false,
        "required": true,
        "schema": {
          "min_length": 1,
          "type": "string"
        }
      },
      "wafer": {
        "nullable": false,
        "required": true,
        "schema": {
          "min_length": 1,
          "type": "string"
        }
      }
    }
  },
  "responses": {
    "200": {
      "schema": {
        "items": {
          "additional_properties": false,
          "fields": {
            "measured_at": {
              "nullable": false,
              "required": true,
              "schema": {
                "format": "date-time",
                "type": "string"
              }
            },
            "recipe_step_no": {
              "nullable": false,
              "required": true,
              "schema": {
                "minimum": 1.0,
                "type": "integer"
              }
            },
            "seq_no": {
              "nullable": false,
              "required": true,
              "schema": {
                "minimum": 0.0,
                "type": "integer"
              }
            },
            "value": {
              "nullable": false,
              "required": true,
              "schema": {
                "type": "number"
              }
            }
          },
          "type": "object"
        },
        "type": "array"
      },
      "shape": "array"
    },
    "422": {
      "schema": {
        "additional_properties": true,
        "fields": {
          "detail": {
            "nullable": false,
            "required": false,
            "schema": {
              "items": {
                "additional_properties": true,
                "fields": {
                  "loc": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "items": {
                        "type": "union",
                        "variants": [
                          {
                            "type": "string"
                          },
                          {
                            "type": "integer"
                          }
                        ]
                      },
                      "type": "array"
                    }
                  },
                  "msg": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "type": "string"
                    }
                  },
                  "type": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "type": "string"
                    }
                  }
                },
                "type": "object"
              },
              "type": "array"
            }
          }
        },
        "type": "object"
      },
      "shape": "object"
    }
  }
}
```

### 4.3 `GET /parameters`

- 구분/담당: 필수 / A
- 요청: 없음
- 성공 응답: ParameterItem[]
- 기타 상태: 503
- 정렬·제약: area ASC; parameter_id ASC
- 호환·경계: 8행; upper_only은 source metadata 근거; name·LSL·LCL·TARGET·UCL·USL은 deprecated alias
- 계약 규칙:
  - 없음

```json
{
  "request": {
    "body": null,
    "header": {},
    "path": {},
    "query": {}
  },
  "responses": {
    "200": {
      "schema": {
        "items": {
          "additional_properties": false,
          "fields": {
            "LCL": {
              "nullable": true,
              "required": false,
              "schema": {
                "type": "number"
              }
            },
            "LSL": {
              "nullable": true,
              "required": false,
              "schema": {
                "type": "number"
              }
            },
            "TARGET": {
              "nullable": false,
              "required": true,
              "schema": {
                "type": "number"
              }
            },
            "UCL": {
              "nullable": false,
              "required": true,
              "schema": {
                "type": "number"
              }
            },
            "USL": {
              "nullable": false,
              "required": true,
              "schema": {
                "type": "number"
              }
            },
            "area": {
              "nullable": false,
              "required": true,
              "schema": {
                "enum": [
                  "Etch",
                  "Photo"
                ],
                "type": "string"
              }
            },
            "ctrl_lower": {
              "nullable": true,
              "required": false,
              "schema": {
                "type": "number"
              }
            },
            "ctrl_upper": {
              "nullable": false,
              "required": true,
              "schema": {
                "type": "number"
              }
            },
            "name": {
              "nullable": false,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            },
            "parameter_id": {
              "nullable": false,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            },
            "parameter_name": {
              "nullable": false,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            },
            "spec_lower": {
              "nullable": true,
              "required": false,
              "schema": {
                "type": "number"
              }
            },
            "spec_upper": {
              "nullable": false,
              "required": true,
              "schema": {
                "type": "number"
              }
            },
            "target_value": {
              "nullable": false,
              "required": true,
              "schema": {
                "type": "number"
              }
            },
            "unit": {
              "nullable": true,
              "required": false,
              "schema": {
                "type": "string"
              }
            },
            "upper_only": {
              "nullable": false,
              "required": true,
              "schema": {
                "type": "boolean"
              }
            }
          },
          "type": "object"
        },
        "type": "array"
      },
      "shape": "array"
    }
  }
}
```

### 4.4 `POST /documents/search`

- 구분/담당: 필수 / B
- 요청: body: query 1..1000; model_code 선택; top_k 1..10 기본4
- 성공 응답: DocumentHit[]
- 기타 상태: 422,503
- 정렬·제약: score DESC; document_id ASC; chunk_id ASC
- 호환·경계: 검증된 corrected RAG source만; 0건=[]; chunk_id=<document_id>:<chunk_schema_version>:<4자리 순번>, 최초 cs1; doc_id는 document_id alias; ① schema·loader·bge-m3 1024 + ③ RAG
- 계약 규칙:
  - 없음

```json
{
  "request": {
    "body": {
      "additional_properties": false,
      "fields": {
        "model_code": {
          "nullable": true,
          "required": false,
          "schema": {
            "min_length": 1,
            "type": "string"
          }
        },
        "query": {
          "nullable": false,
          "required": true,
          "schema": {
            "max_length": 1000,
            "min_length": 1,
            "type": "string"
          }
        },
        "top_k": {
          "nullable": false,
          "required": false,
          "schema": {
            "default": 4,
            "maximum": 10.0,
            "minimum": 1.0,
            "type": "integer"
          }
        }
      },
      "type": "object"
    },
    "header": {},
    "path": {},
    "query": {}
  },
  "responses": {
    "200": {
      "schema": {
        "items": {
          "additional_properties": false,
          "fields": {
            "chunk_id": {
              "nullable": false,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            },
            "content": {
              "nullable": false,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            },
            "doc_id": {
              "nullable": false,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            },
            "document_id": {
              "nullable": false,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            },
            "model_code": {
              "nullable": true,
              "required": false,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            },
            "score": {
              "nullable": false,
              "required": true,
              "schema": {
                "maximum": 1.0,
                "minimum": -1.0,
                "type": "number"
              }
            },
            "section": {
              "nullable": true,
              "required": false,
              "schema": {
                "type": "string"
              }
            },
            "title": {
              "nullable": false,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            }
          },
          "type": "object"
        },
        "type": "array"
      },
      "shape": "array"
    },
    "422": {
      "schema": {
        "additional_properties": true,
        "fields": {
          "detail": {
            "nullable": false,
            "required": false,
            "schema": {
              "items": {
                "additional_properties": true,
                "fields": {
                  "loc": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "items": {
                        "type": "union",
                        "variants": [
                          {
                            "type": "string"
                          },
                          {
                            "type": "integer"
                          }
                        ]
                      },
                      "type": "array"
                    }
                  },
                  "msg": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "type": "string"
                    }
                  },
                  "type": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "type": "string"
                    }
                  }
                },
                "type": "object"
              },
              "type": "array"
            }
          }
        },
        "type": "object"
      },
      "shape": "object"
    }
  }
}
```

### 4.5 `GET /relations/chambers/{chamber_id}`

- 구분/담당: 보안필수 / B
- 요청: path: chamber_id 필수; query: label 선택; limit 1..1000 기본500
- 성공 응답: ChamberGraphResponse
- 기타 상태: 404,422,503
- 정렬·제약: node label/business_id; edge type/from/to
- 호환·경계: chamber component만(Recipe/RecipeStep 제외); relation_id=REL-SHA20; graph_revision=검증 marker actual fingerprint; 응답 count는 subset 배열 길이; 전체 44/85는 graph gate; Neo4j Browser·credentials 직접 노출 금지
- 계약 규칙:
  - node_count equals len(nodes)
  - relationship_count equals len(relationships)

```json
{
  "request": {
    "body": null,
    "header": {},
    "path": {
      "chamber_id": {
        "nullable": false,
        "required": true,
        "schema": {
          "type": "string"
        }
      }
    },
    "query": {}
  },
  "responses": {
    "200": {
      "schema": {
        "additional_properties": false,
        "fields": {
          "graph_revision": {
            "nullable": false,
            "required": true,
            "schema": {
              "min_length": 1,
              "type": "string"
            }
          },
          "nodes": {
            "nullable": false,
            "required": true,
            "schema": {
              "items": {
                "additional_properties": false,
                "fields": {
                  "business_id": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "min_length": 1,
                      "type": "string"
                    }
                  },
                  "display_name": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "min_length": 1,
                      "type": "string"
                    }
                  },
                  "id": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "min_length": 1,
                      "type": "string"
                    }
                  },
                  "label": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "min_length": 1,
                      "type": "string"
                    }
                  },
                  "properties": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "additional_properties": true,
                      "fields": {},
                      "type": "object"
                    }
                  }
                },
                "type": "object"
              },
              "type": "array"
            }
          },
          "relationships": {
            "nullable": false,
            "required": true,
            "schema": {
              "items": {
                "additional_properties": false,
                "fields": {
                  "id": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "min_length": 1,
                      "type": "string"
                    }
                  },
                  "source": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "min_length": 1,
                      "type": "string"
                    }
                  },
                  "target": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "min_length": 1,
                      "type": "string"
                    }
                  },
                  "type": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "min_length": 1,
                      "type": "string"
                    }
                  }
                },
                "type": "object"
              },
              "type": "array"
            }
          },
          "root_node_id": {
            "nullable": false,
            "required": true,
            "schema": {
              "min_length": 1,
              "type": "string"
            }
          }
        },
        "type": "object"
      },
      "shape": "object"
    },
    "422": {
      "schema": {
        "additional_properties": true,
        "fields": {
          "detail": {
            "nullable": false,
            "required": false,
            "schema": {
              "items": {
                "additional_properties": true,
                "fields": {
                  "loc": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "items": {
                        "type": "union",
                        "variants": [
                          {
                            "type": "string"
                          },
                          {
                            "type": "integer"
                          }
                        ]
                      },
                      "type": "array"
                    }
                  },
                  "msg": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "type": "string"
                    }
                  },
                  "type": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "type": "string"
                    }
                  }
                },
                "type": "object"
              },
              "type": "array"
            }
          }
        },
        "type": "object"
      },
      "shape": "object"
    }
  }
}
```

### 4.6 `GET /agent/runs`

- 구분/담당: 필수 / C
- 요청: query: date_from,date_to 선택이며 함께 사용
- 성공 응답: AgentRunItem[]
- 기타 상태: 422,503
- 정렬·제약: created_at DESC; agent_run_id DESC
- 호환·경계: alarm_source+alarm_id; canonical chamber_id + deprecated chamber; action_id+approval_id link; deliveries[]는 public EMAIL|MES projection; Auto Tool alias는 n/s만; 예측 전 fault null은 분석 전 표시·분포 제외
- 계약 규칙:
  - 없음

```json
{
  "request": {
    "body": null,
    "header": {},
    "path": {},
    "query": {
      "date_from": {
        "nullable": true,
        "required": false,
        "schema": {
          "format": "date",
          "type": "string"
        }
      },
      "date_to": {
        "nullable": true,
        "required": false,
        "schema": {
          "format": "date",
          "type": "string"
        }
      },
      "predicted_fault_code": {
        "nullable": true,
        "required": false,
        "schema": {
          "enum": [
            "FOC",
            "MFD",
            "OTH",
            "RFM",
            "TMD"
          ],
          "type": "string"
        }
      },
      "status": {
        "nullable": true,
        "required": false,
        "schema": {
          "enum": [
            "COMPLETED",
            "FAILED",
            "RUNNING",
            "WAITING_APPROVAL"
          ],
          "type": "string"
        }
      }
    }
  },
  "responses": {
    "200": {
      "schema": {
        "items": {
          "additional_properties": false,
          "fields": {
            "action_id": {
              "nullable": true,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            },
            "agent_run_id": {
              "nullable": false,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            },
            "alarm_id": {
              "nullable": false,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            },
            "alarm_source": {
              "nullable": false,
              "required": true,
              "schema": {
                "enum": [
                  "R03",
                  "SUMMARY",
                  "TRACE"
                ],
                "type": "string"
              }
            },
            "approval_id": {
              "nullable": true,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            },
            "chamber": {
              "nullable": false,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            },
            "chamber_id": {
              "nullable": false,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            },
            "confidence": {
              "nullable": true,
              "required": true,
              "schema": {
                "maximum": 1.0,
                "minimum": 0.0,
                "type": "number"
              }
            },
            "created_at": {
              "nullable": false,
              "required": true,
              "schema": {
                "format": "date-time",
                "type": "string"
              }
            },
            "deliveries": {
              "nullable": false,
              "required": true,
              "schema": {
                "items": {
                  "additional_properties": false,
                  "fields": {
                    "channel": {
                      "nullable": false,
                      "required": true,
                      "schema": {
                        "enum": [
                          "EMAIL",
                          "MES"
                        ],
                        "type": "string"
                      }
                    },
                    "status": {
                      "nullable": false,
                      "required": true,
                      "schema": {
                        "enum": [
                          "BLOCKED",
                          "CANCELED",
                          "FAILED",
                          "SENDING",
                          "SENT",
                          "UNKNOWN",
                          "WAITING"
                        ],
                        "type": "string"
                      }
                    }
                  },
                  "type": "object"
                },
                "type": "array"
              }
            },
            "fault_code": {
              "nullable": true,
              "required": true,
              "schema": {
                "enum": [
                  "FOC",
                  "MFD",
                  "OTH",
                  "RFM",
                  "TMD"
                ],
                "type": "string"
              }
            },
            "fault_color": {
              "nullable": false,
              "required": true,
              "schema": {
                "type": "null"
              }
            },
            "fault_name": {
              "nullable": false,
              "required": true,
              "schema": {
                "type": "null"
              }
            },
            "latency_ms": {
              "nullable": false,
              "required": true,
              "schema": {
                "minimum": 0.0,
                "type": "integer"
              }
            },
            "llm_model": {
              "nullable": false,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            },
            "predicted_fault_code": {
              "nullable": true,
              "required": true,
              "schema": {
                "enum": [
                  "FOC",
                  "MFD",
                  "OTH",
                  "RFM",
                  "TMD"
                ],
                "type": "string"
              }
            },
            "recommended_action": {
              "nullable": true,
              "required": true,
              "schema": {
                "enum": [
                  "EQP_HOLD",
                  "MONITORING",
                  "WARNING"
                ],
                "type": "string"
              }
            },
            "status": {
              "nullable": false,
              "required": true,
              "schema": {
                "enum": [
                  "COMPLETED",
                  "FAILED",
                  "RUNNING",
                  "WAITING_APPROVAL"
                ],
                "type": "string"
              }
            },
            "tools": {
              "nullable": false,
              "required": true,
              "schema": {
                "items": {
                  "additional_properties": false,
                  "fields": {
                    "n": {
                      "nullable": false,
                      "required": true,
                      "schema": {
                        "min_length": 1,
                        "type": "string"
                      }
                    },
                    "result_summary": {
                      "nullable": false,
                      "required": true,
                      "schema": {
                        "min_length": 1,
                        "type": "string"
                      }
                    },
                    "s": {
                      "nullable": false,
                      "required": true,
                      "schema": {
                        "enum": [
                          "ERROR",
                          "SUCCESS",
                          "TIMEOUT"
                        ],
                        "type": "string"
                      }
                    },
                    "status": {
                      "nullable": false,
                      "required": true,
                      "schema": {
                        "enum": [
                          "ERROR",
                          "SUCCESS",
                          "TIMEOUT"
                        ],
                        "type": "string"
                      }
                    },
                    "tool_name": {
                      "nullable": false,
                      "required": true,
                      "schema": {
                        "min_length": 1,
                        "type": "string"
                      }
                    }
                  },
                  "type": "object"
                },
                "type": "array"
              }
            }
          },
          "type": "object"
        },
        "type": "array"
      },
      "shape": "array"
    },
    "422": {
      "schema": {
        "additional_properties": true,
        "fields": {
          "detail": {
            "nullable": false,
            "required": false,
            "schema": {
              "items": {
                "additional_properties": true,
                "fields": {
                  "loc": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "items": {
                        "type": "union",
                        "variants": [
                          {
                            "type": "string"
                          },
                          {
                            "type": "integer"
                          }
                        ]
                      },
                      "type": "array"
                    }
                  },
                  "msg": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "type": "string"
                    }
                  },
                  "type": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "type": "string"
                    }
                  }
                },
                "type": "object"
              },
              "type": "array"
            }
          }
        },
        "type": "object"
      },
      "shape": "object"
    }
  }
}
```

### 4.7 `POST /agent/ask`

- 구분/담당: 필수 / C
- 요청: body: question 1..1000
- 성공 응답: AgentAskResponse
- 기타 상태: 422,503
- 정렬·제약: 읽기 전용
- 호환·경계: tools·evidence_items·limitations와 predicted_fault_code·confidence·recommended_action은 항상 존재(후 3개 required-nullable); Chat Tool alias는 name/result만; fault_code 없음; DOCUMENT document_id·chunk_id 필수/section required-nullable; GRAPH relation_id·graph_revision 필수; METROLOGY alarm_result 미노출; evidence·limit·doc_id는 deprecated alias; action·approval write 없음
- 계약 규칙:
  - fault_code and ground_truth_fault_code are forbidden
  - metrology.alarm_result is forbidden

```json
{
  "request": {
    "body": {
      "additional_properties": false,
      "fields": {
        "question": {
          "nullable": false,
          "required": true,
          "schema": {
            "max_length": 1000,
            "min_length": 1,
            "type": "string"
          }
        }
      },
      "type": "object"
    },
    "header": {},
    "path": {},
    "query": {}
  },
  "responses": {
    "200": {
      "schema": {
        "additional_properties": false,
        "fields": {
          "answer": {
            "nullable": false,
            "required": true,
            "schema": {
              "min_length": 1,
              "type": "string"
            }
          },
          "confidence": {
            "nullable": true,
            "required": true,
            "schema": {
              "maximum": 1.0,
              "minimum": 0.0,
              "type": "number"
            }
          },
          "evidence": {
            "nullable": true,
            "required": true,
            "schema": {
              "additional_properties": false,
              "fields": {
                "chunk_id": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "min_length": 1,
                    "type": "string"
                  }
                },
                "doc_id": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "min_length": 1,
                    "type": "string"
                  }
                },
                "document_id": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "min_length": 1,
                    "type": "string"
                  }
                },
                "section": {
                  "nullable": true,
                  "required": true,
                  "schema": {
                    "type": "string"
                  }
                }
              },
              "type": "object"
            }
          },
          "evidence_items": {
            "nullable": false,
            "required": true,
            "schema": {
              "items": {
                "discriminator": "type",
                "type": "discriminated_union",
                "variants": {
                  "ALARM": {
                    "additional_properties": false,
                    "fields": {
                      "excerpt": {
                        "nullable": false,
                        "required": true,
                        "schema": {
                          "min_length": 1,
                          "type": "string"
                        }
                      },
                      "source_id": {
                        "nullable": false,
                        "required": true,
                        "schema": {
                          "min_length": 1,
                          "type": "string"
                        }
                      },
                      "title": {
                        "nullable": false,
                        "required": true,
                        "schema": {
                          "min_length": 1,
                          "type": "string"
                        }
                      },
                      "type": {
                        "nullable": false,
                        "required": true,
                        "schema": {
                          "type": "string"
                        }
                      }
                    },
                    "type": "object"
                  },
                  "DOCUMENT": {
                    "additional_properties": false,
                    "fields": {
                      "chunk_id": {
                        "nullable": false,
                        "required": true,
                        "schema": {
                          "min_length": 1,
                          "type": "string"
                        }
                      },
                      "document_id": {
                        "nullable": false,
                        "required": true,
                        "schema": {
                          "min_length": 1,
                          "type": "string"
                        }
                      },
                      "excerpt": {
                        "nullable": false,
                        "required": true,
                        "schema": {
                          "min_length": 1,
                          "type": "string"
                        }
                      },
                      "section": {
                        "nullable": true,
                        "required": true,
                        "schema": {
                          "type": "string"
                        }
                      },
                      "source_id": {
                        "nullable": false,
                        "required": true,
                        "schema": {
                          "min_length": 1,
                          "type": "string"
                        }
                      },
                      "title": {
                        "nullable": false,
                        "required": true,
                        "schema": {
                          "min_length": 1,
                          "type": "string"
                        }
                      },
                      "type": {
                        "nullable": false,
                        "required": true,
                        "schema": {
                          "type": "string"
                        }
                      }
                    },
                    "type": "object"
                  },
                  "GRAPH": {
                    "additional_properties": false,
                    "fields": {
                      "excerpt": {
                        "nullable": false,
                        "required": true,
                        "schema": {
                          "min_length": 1,
                          "type": "string"
                        }
                      },
                      "graph_revision": {
                        "nullable": false,
                        "required": true,
                        "schema": {
                          "min_length": 1,
                          "type": "string"
                        }
                      },
                      "relation_id": {
                        "nullable": false,
                        "required": true,
                        "schema": {
                          "min_length": 1,
                          "type": "string"
                        }
                      },
                      "source_id": {
                        "nullable": false,
                        "required": true,
                        "schema": {
                          "min_length": 1,
                          "type": "string"
                        }
                      },
                      "title": {
                        "nullable": false,
                        "required": true,
                        "schema": {
                          "min_length": 1,
                          "type": "string"
                        }
                      },
                      "type": {
                        "nullable": false,
                        "required": true,
                        "schema": {
                          "type": "string"
                        }
                      }
                    },
                    "type": "object"
                  },
                  "METROLOGY": {
                    "additional_properties": false,
                    "fields": {
                      "excerpt": {
                        "nullable": false,
                        "required": true,
                        "schema": {
                          "min_length": 1,
                          "type": "string"
                        }
                      },
                      "source_id": {
                        "nullable": false,
                        "required": true,
                        "schema": {
                          "min_length": 1,
                          "type": "string"
                        }
                      },
                      "title": {
                        "nullable": false,
                        "required": true,
                        "schema": {
                          "min_length": 1,
                          "type": "string"
                        }
                      },
                      "type": {
                        "nullable": false,
                        "required": true,
                        "schema": {
                          "type": "string"
                        }
                      }
                    },
                    "type": "object"
                  },
                  "TRACE": {
                    "additional_properties": false,
                    "fields": {
                      "excerpt": {
                        "nullable": false,
                        "required": true,
                        "schema": {
                          "min_length": 1,
                          "type": "string"
                        }
                      },
                      "source_id": {
                        "nullable": false,
                        "required": true,
                        "schema": {
                          "min_length": 1,
                          "type": "string"
                        }
                      },
                      "title": {
                        "nullable": false,
                        "required": true,
                        "schema": {
                          "min_length": 1,
                          "type": "string"
                        }
                      },
                      "type": {
                        "nullable": false,
                        "required": true,
                        "schema": {
                          "type": "string"
                        }
                      }
                    },
                    "type": "object"
                  }
                }
              },
              "type": "array"
            }
          },
          "limit": {
            "nullable": false,
            "required": true,
            "schema": {
              "type": "string"
            }
          },
          "limitations": {
            "nullable": false,
            "required": true,
            "schema": {
              "items": {
                "type": "string"
              },
              "type": "array"
            }
          },
          "predicted_fault_code": {
            "nullable": true,
            "required": true,
            "schema": {
              "enum": [
                "FOC",
                "MFD",
                "OTH",
                "RFM",
                "TMD"
              ],
              "type": "string"
            }
          },
          "recommended_action": {
            "nullable": true,
            "required": true,
            "schema": {
              "enum": [
                "EQP_HOLD",
                "MONITORING",
                "WARNING"
              ],
              "type": "string"
            }
          },
          "title": {
            "nullable": false,
            "required": true,
            "schema": {
              "min_length": 1,
              "type": "string"
            }
          },
          "tools": {
            "nullable": false,
            "required": true,
            "schema": {
              "items": {
                "additional_properties": false,
                "fields": {
                  "name": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "min_length": 1,
                      "type": "string"
                    }
                  },
                  "result": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "min_length": 1,
                      "type": "string"
                    }
                  },
                  "result_summary": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "min_length": 1,
                      "type": "string"
                    }
                  },
                  "status": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "enum": [
                        "ERROR",
                        "SUCCESS",
                        "TIMEOUT"
                      ],
                      "type": "string"
                    }
                  },
                  "tool_name": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "min_length": 1,
                      "type": "string"
                    }
                  }
                },
                "type": "object"
              },
              "type": "array"
            }
          }
        },
        "type": "object"
      },
      "shape": "object"
    },
    "422": {
      "schema": {
        "additional_properties": true,
        "fields": {
          "detail": {
            "nullable": false,
            "required": false,
            "schema": {
              "items": {
                "additional_properties": true,
                "fields": {
                  "loc": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "items": {
                        "type": "union",
                        "variants": [
                          {
                            "type": "string"
                          },
                          {
                            "type": "integer"
                          }
                        ]
                      },
                      "type": "array"
                    }
                  },
                  "msg": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "type": "string"
                    }
                  },
                  "type": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "type": "string"
                    }
                  }
                },
                "type": "object"
              },
              "type": "array"
            }
          }
        },
        "type": "object"
      },
      "shape": "object"
    }
  }
}
```

### 4.8 `GET /approvals`

- 구분/담당: 필수 / C
- 요청: 없음
- 성공 응답: ApprovalItem[]
- 기타 상태: 503
- 정렬·제약: created_at DESC; approval_id DESC
- 호환·경계: EQP_HOLD만; PENDING|APPROVED|REJECTED; canonical lot_id,equipment_id,chamber_id + deprecated 축약 alias; run/action ID link와 canonical decided field
- 계약 규칙:
  - APPROVED or REJECTED requires decided_by and decided_at
  - PENDING requires all decision fields null
  - only EQP_HOLD actions have approvals

```json
{
  "request": {
    "body": null,
    "header": {},
    "path": {},
    "query": {}
  },
  "responses": {
    "200": {
      "schema": {
        "items": {
          "additional_properties": false,
          "fields": {
            "action_code": {
              "nullable": false,
              "required": true,
              "schema": {
                "enum": [
                  "EQP_HOLD",
                  "MONITORING",
                  "WARNING"
                ],
                "type": "string"
              }
            },
            "action_id": {
              "nullable": false,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            },
            "agent_run_id": {
              "nullable": false,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            },
            "approval_id": {
              "nullable": false,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            },
            "approved_at": {
              "nullable": true,
              "required": true,
              "schema": {
                "format": "date-time",
                "type": "string"
              }
            },
            "approved_by": {
              "nullable": true,
              "required": true,
              "schema": {
                "type": "string"
              }
            },
            "chamber": {
              "nullable": false,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            },
            "chamber_id": {
              "nullable": false,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            },
            "created_at": {
              "nullable": false,
              "required": true,
              "schema": {
                "format": "date-time",
                "type": "string"
              }
            },
            "decided_at": {
              "nullable": true,
              "required": true,
              "schema": {
                "format": "date-time",
                "type": "string"
              }
            },
            "decided_by": {
              "nullable": true,
              "required": true,
              "schema": {
                "type": "string"
              }
            },
            "decision_comment": {
              "nullable": true,
              "required": true,
              "schema": {
                "type": "string"
              }
            },
            "equipment": {
              "nullable": false,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            },
            "equipment_id": {
              "nullable": false,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            },
            "fault_code": {
              "nullable": false,
              "required": true,
              "schema": {
                "enum": [
                  "FOC",
                  "MFD",
                  "OTH",
                  "RFM",
                  "TMD"
                ],
                "type": "string"
              }
            },
            "lot": {
              "nullable": false,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            },
            "lot_id": {
              "nullable": false,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            },
            "predicted_fault_code": {
              "nullable": false,
              "required": true,
              "schema": {
                "enum": [
                  "FOC",
                  "MFD",
                  "OTH",
                  "RFM",
                  "TMD"
                ],
                "type": "string"
              }
            },
            "reason": {
              "nullable": false,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            },
            "status": {
              "nullable": false,
              "required": true,
              "schema": {
                "enum": [
                  "APPROVED",
                  "PENDING",
                  "REJECTED"
                ],
                "type": "string"
              }
            }
          },
          "type": "object"
        },
        "type": "array"
      },
      "shape": "array"
    }
  }
}
```

### 4.9 `POST /approvals/{approval_id}/decision`

- 구분/담당: 필수 / C
- 요청: body: decision=APPROVED|REJECTED; decided_by; decision_comment 선택 1..1000
- 성공 응답: ApprovalItem
- 기타 상태: 404,409,422,503
- 정렬·제약: 단일 상태 전이
- 호환·경계: canonical decided_by/at/comment; public APPROVED|REJECTED를 internal APPROVE|REJECT로 adapter; 승인 후 Kafka MES Mock
- 계약 규칙:
  - APPROVED or REJECTED requires decided_by and decided_at
  - PENDING requires all decision fields null
  - only EQP_HOLD actions have approvals

```json
{
  "request": {
    "body": {
      "additional_properties": false,
      "fields": {
        "decided_by": {
          "nullable": false,
          "required": true,
          "schema": {
            "max_length": 40,
            "min_length": 1,
            "type": "string"
          }
        },
        "decision": {
          "nullable": false,
          "required": true,
          "schema": {
            "enum": [
              "APPROVED",
              "REJECTED"
            ],
            "type": "string"
          }
        },
        "decision_comment": {
          "nullable": true,
          "required": false,
          "schema": {
            "max_length": 1000,
            "type": "string"
          }
        }
      },
      "type": "object"
    },
    "header": {},
    "path": {
      "approval_id": {
        "nullable": false,
        "required": true,
        "schema": {
          "type": "string"
        }
      }
    },
    "query": {}
  },
  "responses": {
    "200": {
      "schema": {
        "additional_properties": false,
        "fields": {
          "action_code": {
            "nullable": false,
            "required": true,
            "schema": {
              "enum": [
                "EQP_HOLD",
                "MONITORING",
                "WARNING"
              ],
              "type": "string"
            }
          },
          "action_id": {
            "nullable": false,
            "required": true,
            "schema": {
              "min_length": 1,
              "type": "string"
            }
          },
          "agent_run_id": {
            "nullable": false,
            "required": true,
            "schema": {
              "min_length": 1,
              "type": "string"
            }
          },
          "approval_id": {
            "nullable": false,
            "required": true,
            "schema": {
              "min_length": 1,
              "type": "string"
            }
          },
          "approved_at": {
            "nullable": true,
            "required": true,
            "schema": {
              "format": "date-time",
              "type": "string"
            }
          },
          "approved_by": {
            "nullable": true,
            "required": true,
            "schema": {
              "type": "string"
            }
          },
          "chamber": {
            "nullable": false,
            "required": true,
            "schema": {
              "min_length": 1,
              "type": "string"
            }
          },
          "chamber_id": {
            "nullable": false,
            "required": true,
            "schema": {
              "min_length": 1,
              "type": "string"
            }
          },
          "created_at": {
            "nullable": false,
            "required": true,
            "schema": {
              "format": "date-time",
              "type": "string"
            }
          },
          "decided_at": {
            "nullable": true,
            "required": true,
            "schema": {
              "format": "date-time",
              "type": "string"
            }
          },
          "decided_by": {
            "nullable": true,
            "required": true,
            "schema": {
              "type": "string"
            }
          },
          "decision_comment": {
            "nullable": true,
            "required": true,
            "schema": {
              "type": "string"
            }
          },
          "equipment": {
            "nullable": false,
            "required": true,
            "schema": {
              "min_length": 1,
              "type": "string"
            }
          },
          "equipment_id": {
            "nullable": false,
            "required": true,
            "schema": {
              "min_length": 1,
              "type": "string"
            }
          },
          "fault_code": {
            "nullable": false,
            "required": true,
            "schema": {
              "enum": [
                "FOC",
                "MFD",
                "OTH",
                "RFM",
                "TMD"
              ],
              "type": "string"
            }
          },
          "lot": {
            "nullable": false,
            "required": true,
            "schema": {
              "min_length": 1,
              "type": "string"
            }
          },
          "lot_id": {
            "nullable": false,
            "required": true,
            "schema": {
              "min_length": 1,
              "type": "string"
            }
          },
          "predicted_fault_code": {
            "nullable": false,
            "required": true,
            "schema": {
              "enum": [
                "FOC",
                "MFD",
                "OTH",
                "RFM",
                "TMD"
              ],
              "type": "string"
            }
          },
          "reason": {
            "nullable": false,
            "required": true,
            "schema": {
              "min_length": 1,
              "type": "string"
            }
          },
          "status": {
            "nullable": false,
            "required": true,
            "schema": {
              "enum": [
                "APPROVED",
                "PENDING",
                "REJECTED"
              ],
              "type": "string"
            }
          }
        },
        "type": "object"
      },
      "shape": "object"
    },
    "422": {
      "schema": {
        "additional_properties": true,
        "fields": {
          "detail": {
            "nullable": false,
            "required": false,
            "schema": {
              "items": {
                "additional_properties": true,
                "fields": {
                  "loc": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "items": {
                        "type": "union",
                        "variants": [
                          {
                            "type": "string"
                          },
                          {
                            "type": "integer"
                          }
                        ]
                      },
                      "type": "array"
                    }
                  },
                  "msg": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "type": "string"
                    }
                  },
                  "type": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "type": "string"
                    }
                  }
                },
                "type": "object"
              },
              "type": "array"
            }
          }
        },
        "type": "object"
      },
      "shape": "object"
    }
  }
}
```

### 4.10 `GET /audit-logs`

- 구분/담당: 필수 / D
- 요청: query: date range,event,actor,entity 선택
- 성공 응답: AuditLogItem[]
- 기타 상태: 422,503
- 정렬·제약: occurred_at DESC; audit_id DESC
- 호환·경계: Common 계약; 도메인 기록; D 조회; at·actor·event·entity는 deprecated alias; write API 없음
- 계약 규칙:
  - 없음

```json
{
  "request": {
    "body": null,
    "header": {},
    "path": {},
    "query": {
      "actor_type": {
        "nullable": true,
        "required": false,
        "schema": {
          "type": "string"
        }
      },
      "date_from": {
        "nullable": true,
        "required": false,
        "schema": {
          "format": "date",
          "type": "string"
        }
      },
      "date_to": {
        "nullable": true,
        "required": false,
        "schema": {
          "format": "date",
          "type": "string"
        }
      },
      "entity_id": {
        "nullable": true,
        "required": false,
        "schema": {
          "type": "string"
        }
      },
      "entity_type": {
        "nullable": true,
        "required": false,
        "schema": {
          "type": "string"
        }
      },
      "event_type": {
        "nullable": true,
        "required": false,
        "schema": {
          "type": "string"
        }
      }
    }
  },
  "responses": {
    "200": {
      "schema": {
        "items": {
          "additional_properties": true,
          "fields": {
            "actor": {
              "nullable": false,
              "required": true,
              "schema": {
                "type": "string"
              }
            },
            "actor_id": {
              "nullable": true,
              "required": true,
              "schema": {
                "type": "string"
              }
            },
            "actor_type": {
              "nullable": false,
              "required": true,
              "schema": {
                "type": "string"
              }
            },
            "after": {
              "nullable": true,
              "required": true,
              "schema": {
                "additional_properties": true,
                "fields": {},
                "type": "object"
              }
            },
            "at": {
              "nullable": false,
              "required": true,
              "schema": {
                "format": "date-time",
                "type": "string"
              }
            },
            "audit_id": {
              "nullable": false,
              "required": true,
              "schema": {
                "type": "integer"
              }
            },
            "before": {
              "nullable": true,
              "required": true,
              "schema": {
                "additional_properties": true,
                "fields": {},
                "type": "object"
              }
            },
            "detail": {
              "nullable": true,
              "required": true,
              "schema": {
                "type": "string"
              }
            },
            "entity": {
              "nullable": false,
              "required": true,
              "schema": {
                "type": "string"
              }
            },
            "entity_id": {
              "nullable": false,
              "required": true,
              "schema": {
                "type": "string"
              }
            },
            "entity_type": {
              "nullable": false,
              "required": true,
              "schema": {
                "type": "string"
              }
            },
            "event": {
              "nullable": false,
              "required": true,
              "schema": {
                "type": "string"
              }
            },
            "event_type": {
              "nullable": false,
              "required": true,
              "schema": {
                "type": "string"
              }
            },
            "occurred_at": {
              "nullable": false,
              "required": true,
              "schema": {
                "format": "date-time",
                "type": "string"
              }
            }
          },
          "type": "object"
        },
        "type": "array"
      },
      "shape": "array"
    },
    "422": {
      "schema": {
        "additional_properties": true,
        "fields": {
          "detail": {
            "nullable": false,
            "required": false,
            "schema": {
              "items": {
                "additional_properties": true,
                "fields": {
                  "loc": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "items": {
                        "type": "union",
                        "variants": [
                          {
                            "type": "string"
                          },
                          {
                            "type": "integer"
                          }
                        ]
                      },
                      "type": "array"
                    }
                  },
                  "msg": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "type": "string"
                    }
                  },
                  "type": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "type": "string"
                    }
                  }
                },
                "type": "object"
              },
              "type": "array"
            }
          }
        },
        "type": "object"
      },
      "shape": "object"
    }
  }
}
```

### 4.11 `GET /dataset/bounds`

- 구분/담당: 확장 / A
- 요청: 없음
- 성공 응답: DatasetBoundsResponse
- 기타 상태: 503
- 정렬·제약: 안정 정렬
- 호환·경계: 최소 9개 이후
- 계약 규칙:
  - 없음

> deferred inventory: semantic schema는 owner 구현 Task에서 비준한다.

### 4.12 `GET /dashboard/summary`

- 구분/담당: 확장 / A
- 요청: query: date_from,date_to,area 필수
- 성공 응답: DashboardSummaryResponse
- 기타 상태: 422,503
- 정렬·제약: 동일 필터 기준
- 호환·경계: 최소 GET /alarms 클라이언트 집계를 대체하지 않음
- 계약 규칙:
  - 없음

```json
{
  "request": {
    "body": null,
    "header": {},
    "path": {},
    "query": {
      "area": {
        "nullable": true,
        "required": false,
        "schema": {
          "enum": [
            "ALL",
            "Etch",
            "Photo"
          ],
          "type": "string"
        }
      },
      "chamber_id": {
        "nullable": true,
        "required": false,
        "schema": {
          "type": "string"
        }
      },
      "date": {
        "nullable": true,
        "required": false,
        "schema": {
          "format": "date",
          "type": "string"
        }
      },
      "equipment_id": {
        "nullable": true,
        "required": false,
        "schema": {
          "type": "string"
        }
      }
    }
  },
  "responses": {
    "200": {
      "schema": {
        "additional_properties": false,
        "fields": {
          "alarm_count": {
            "nullable": false,
            "required": true,
            "schema": {
              "minimum": 0.0,
              "type": "integer"
            }
          },
          "area": {
            "nullable": true,
            "required": true,
            "schema": {
              "enum": [
                "Etch",
                "Photo"
              ],
              "type": "string"
            }
          },
          "daily_trend": {
            "nullable": false,
            "required": true,
            "schema": {
              "items": {
                "additional_properties": false,
                "fields": {
                  "date": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "format": "date",
                      "type": "string"
                    }
                  },
                  "has_r03_consec": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "type": "boolean"
                    }
                  },
                  "ooc_count": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "minimum": 0.0,
                      "type": "integer"
                    }
                  },
                  "oos_count": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "minimum": 0.0,
                      "type": "integer"
                    }
                  }
                },
                "type": "object"
              },
              "type": "array"
            }
          },
          "date_range": {
            "nullable": false,
            "required": true,
            "schema": {
              "items": {
                "format": "date",
                "type": "string"
              },
              "type": "array"
            }
          },
          "equipment_counts": {
            "nullable": false,
            "required": true,
            "schema": {
              "items": {
                "additional_properties": false,
                "fields": {
                  "alarm_count": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "minimum": 0.0,
                      "type": "integer"
                    }
                  },
                  "area_id": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "enum": [
                        "Etch",
                        "Photo"
                      ],
                      "type": "string"
                    }
                  },
                  "chambers": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "items": {
                        "additional_properties": false,
                        "fields": {
                          "alarm_count": {
                            "nullable": false,
                            "required": true,
                            "schema": {
                              "minimum": 0.0,
                              "type": "integer"
                            }
                          },
                          "chamber_id": {
                            "nullable": false,
                            "required": true,
                            "schema": {
                              "min_length": 1,
                              "type": "string"
                            }
                          }
                        },
                        "type": "object"
                      },
                      "type": "array"
                    }
                  },
                  "equipment_id": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "min_length": 1,
                      "type": "string"
                    }
                  }
                },
                "type": "object"
              },
              "type": "array"
            }
          },
          "hierarchy": {
            "nullable": false,
            "required": true,
            "schema": {
              "items": {
                "additional_properties": false,
                "fields": {
                  "area_id": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "enum": [
                        "Etch",
                        "Photo"
                      ],
                      "type": "string"
                    }
                  },
                  "chambers": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "items": {
                        "min_length": 1,
                        "type": "string"
                      },
                      "type": "array"
                    }
                  },
                  "equipment_id": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "min_length": 1,
                      "type": "string"
                    }
                  }
                },
                "type": "object"
              },
              "type": "array"
            }
          },
          "ooc_count": {
            "nullable": false,
            "required": true,
            "schema": {
              "minimum": 0.0,
              "type": "integer"
            }
          },
          "oos_count": {
            "nullable": false,
            "required": true,
            "schema": {
              "minimum": 0.0,
              "type": "integer"
            }
          },
          "pending_approvals": {
            "nullable": false,
            "required": true,
            "schema": {
              "items": {
                "additional_properties": false,
                "fields": {
                  "action_code": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "enum": [
                        "EQP_HOLD",
                        "MONITORING",
                        "WARNING"
                      ],
                      "type": "string"
                    }
                  },
                  "action_id": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "min_length": 1,
                      "type": "string"
                    }
                  },
                  "agent_run_id": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "min_length": 1,
                      "type": "string"
                    }
                  },
                  "approval_id": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "min_length": 1,
                      "type": "string"
                    }
                  },
                  "equipment_id": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "min_length": 1,
                      "type": "string"
                    }
                  },
                  "incident": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "additional_properties": false,
                      "fields": {
                        "chamber_id": {
                          "nullable": false,
                          "required": true,
                          "schema": {
                            "min_length": 1,
                            "type": "string"
                          }
                        },
                        "lot_id": {
                          "nullable": false,
                          "required": true,
                          "schema": {
                            "min_length": 1,
                            "type": "string"
                          }
                        }
                      },
                      "type": "object"
                    }
                  },
                  "requested_at": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "format": "date-time",
                      "type": "string"
                    }
                  },
                  "severity": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "enum": [
                        "HIGH",
                        "LOW",
                        "MEDIUM"
                      ],
                      "type": "string"
                    }
                  }
                },
                "type": "object"
              },
              "type": "array"
            }
          },
          "recent_alarms": {
            "nullable": false,
            "required": true,
            "schema": {
              "items": {
                "additional_properties": false,
                "fields": {
                  "action_code": {
                    "nullable": true,
                    "required": false,
                    "schema": {
                      "enum": [
                        "EQP_HOLD",
                        "MONITORING",
                        "WARNING"
                      ],
                      "type": "string"
                    }
                  },
                  "action_id": {
                    "nullable": true,
                    "required": false,
                    "schema": {
                      "min_length": 1,
                      "type": "string"
                    }
                  },
                  "agent_run_status": {
                    "nullable": true,
                    "required": false,
                    "schema": {
                      "enum": [
                        "COMPLETED",
                        "FAILED",
                        "RUNNING",
                        "WAITING_APPROVAL"
                      ],
                      "type": "string"
                    }
                  },
                  "alarm_id": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "min_length": 1,
                      "type": "string"
                    }
                  },
                  "approval_status": {
                    "nullable": true,
                    "required": false,
                    "schema": {
                      "enum": [
                        "APPROVED",
                        "AUTO",
                        "EXPIRED",
                        "PENDING",
                        "REJECTED"
                      ],
                      "type": "string"
                    }
                  },
                  "area": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "enum": [
                        "Etch",
                        "Photo"
                      ],
                      "type": "string"
                    }
                  },
                  "chamber_id": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "min_length": 1,
                      "type": "string"
                    }
                  },
                  "detail": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "type": "string"
                    }
                  },
                  "equipment_id": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "min_length": 1,
                      "type": "string"
                    }
                  },
                  "hit_cnt": {
                    "nullable": true,
                    "required": false,
                    "schema": {
                      "minimum": 0.0,
                      "type": "integer"
                    }
                  },
                  "incident": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "additional_properties": false,
                      "fields": {
                        "chamber_id": {
                          "nullable": false,
                          "required": true,
                          "schema": {
                            "min_length": 1,
                            "type": "string"
                          }
                        },
                        "lot_id": {
                          "nullable": false,
                          "required": true,
                          "schema": {
                            "min_length": 1,
                            "type": "string"
                          }
                        }
                      },
                      "type": "object"
                    }
                  },
                  "judgement": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "enum": [
                        "OOC",
                        "OOS"
                      ],
                      "type": "string"
                    }
                  },
                  "latest_agent_run_id": {
                    "nullable": true,
                    "required": false,
                    "schema": {
                      "min_length": 1,
                      "type": "string"
                    }
                  },
                  "lot_hist_id": {
                    "nullable": true,
                    "required": false,
                    "schema": {
                      "min_length": 1,
                      "type": "string"
                    }
                  },
                  "lot_id": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "min_length": 1,
                      "type": "string"
                    }
                  },
                  "occurred_at": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "format": "date-time",
                      "type": "string"
                    }
                  },
                  "recipe_step_name": {
                    "nullable": true,
                    "required": false,
                    "schema": {
                      "type": "string"
                    }
                  },
                  "recipe_step_no": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "minimum": 1.0,
                      "type": "integer"
                    }
                  },
                  "rule_id": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "enum": [
                        "R01_OOS",
                        "R02_OOC",
                        "R03_CONSEC"
                      ],
                      "type": "string"
                    }
                  },
                  "sensor_id": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "min_length": 1,
                      "type": "string"
                    }
                  },
                  "source": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "enum": [
                        "R03",
                        "SUMMARY",
                        "TRACE"
                      ],
                      "type": "string"
                    }
                  },
                  "wafer_no": {
                    "nullable": true,
                    "required": false,
                    "schema": {
                      "minimum": 1.0,
                      "type": "integer"
                    }
                  }
                },
                "type": "object"
              },
              "type": "array"
            }
          },
          "reference_date": {
            "nullable": false,
            "required": true,
            "schema": {
              "format": "date",
              "type": "string"
            }
          },
          "sensor_catalog": {
            "nullable": false,
            "required": true,
            "schema": {
              "items": {
                "min_length": 1,
                "type": "string"
              },
              "type": "array"
            }
          },
          "top_sensors": {
            "nullable": false,
            "required": true,
            "schema": {
              "items": {
                "additional_properties": false,
                "fields": {
                  "alarm_count": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "minimum": 0.0,
                      "type": "integer"
                    }
                  },
                  "chamber_ids": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "items": {
                        "min_length": 1,
                        "type": "string"
                      },
                      "type": "array"
                    }
                  },
                  "sensor_id": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "min_length": 1,
                      "type": "string"
                    }
                  }
                },
                "type": "object"
              },
              "type": "array"
            }
          }
        },
        "type": "object"
      },
      "shape": "object"
    },
    "422": {
      "schema": {
        "additional_properties": true,
        "fields": {
          "detail": {
            "nullable": false,
            "required": false,
            "schema": {
              "items": {
                "additional_properties": true,
                "fields": {
                  "loc": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "items": {
                        "type": "union",
                        "variants": [
                          {
                            "type": "string"
                          },
                          {
                            "type": "integer"
                          }
                        ]
                      },
                      "type": "array"
                    }
                  },
                  "msg": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "type": "string"
                    }
                  },
                  "type": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "type": "string"
                    }
                  }
                },
                "type": "object"
              },
              "type": "array"
            }
          }
        },
        "type": "object"
      },
      "shape": "object"
    }
  }
}
```

### 4.13 `GET /alarms/{source}/{alarm_id}`

- 구분/담당: 확장 / A
- 요청: path: source+alarm_id
- 성공 응답: AlarmDetailResponse
- 기타 상태: 404,422,503
- 정렬·제약: 단건
- 호환·경계: source 필수
- 계약 규칙:
  - 없음

```json
{
  "request": {
    "body": null,
    "header": {},
    "path": {
      "alarm_id": {
        "nullable": false,
        "required": true,
        "schema": {
          "type": "string"
        }
      },
      "source": {
        "nullable": false,
        "required": true,
        "schema": {
          "enum": [
            "R03",
            "SUMMARY",
            "TRACE"
          ],
          "type": "string"
        }
      }
    },
    "query": {}
  },
  "responses": {
    "200": {
      "schema": {
        "additional_properties": false,
        "fields": {
          "action_code": {
            "nullable": true,
            "required": false,
            "schema": {
              "enum": [
                "EQP_HOLD",
                "MONITORING",
                "WARNING"
              ],
              "type": "string"
            }
          },
          "action_id": {
            "nullable": true,
            "required": false,
            "schema": {
              "min_length": 1,
              "type": "string"
            }
          },
          "agent_run_status": {
            "nullable": true,
            "required": false,
            "schema": {
              "enum": [
                "COMPLETED",
                "FAILED",
                "RUNNING",
                "WAITING_APPROVAL"
              ],
              "type": "string"
            }
          },
          "alarm_id": {
            "nullable": false,
            "required": true,
            "schema": {
              "min_length": 1,
              "type": "string"
            }
          },
          "approval_status": {
            "nullable": true,
            "required": false,
            "schema": {
              "enum": [
                "APPROVED",
                "AUTO",
                "EXPIRED",
                "PENDING",
                "REJECTED"
              ],
              "type": "string"
            }
          },
          "area": {
            "nullable": false,
            "required": true,
            "schema": {
              "enum": [
                "Etch",
                "Photo"
              ],
              "type": "string"
            }
          },
          "chamber_id": {
            "nullable": false,
            "required": true,
            "schema": {
              "min_length": 1,
              "type": "string"
            }
          },
          "detail": {
            "nullable": false,
            "required": true,
            "schema": {
              "type": "string"
            }
          },
          "equipment_id": {
            "nullable": false,
            "required": true,
            "schema": {
              "min_length": 1,
              "type": "string"
            }
          },
          "hit_cnt": {
            "nullable": true,
            "required": false,
            "schema": {
              "minimum": 0.0,
              "type": "integer"
            }
          },
          "incident": {
            "nullable": false,
            "required": true,
            "schema": {
              "additional_properties": false,
              "fields": {
                "chamber_id": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "min_length": 1,
                    "type": "string"
                  }
                },
                "lot_id": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "min_length": 1,
                    "type": "string"
                  }
                }
              },
              "type": "object"
            }
          },
          "judgement": {
            "nullable": false,
            "required": true,
            "schema": {
              "enum": [
                "OOC",
                "OOS"
              ],
              "type": "string"
            }
          },
          "latest_agent_run_id": {
            "nullable": true,
            "required": false,
            "schema": {
              "min_length": 1,
              "type": "string"
            }
          },
          "lot_hist_id": {
            "nullable": true,
            "required": false,
            "schema": {
              "min_length": 1,
              "type": "string"
            }
          },
          "lot_id": {
            "nullable": false,
            "required": true,
            "schema": {
              "min_length": 1,
              "type": "string"
            }
          },
          "occurred_at": {
            "nullable": false,
            "required": true,
            "schema": {
              "format": "date-time",
              "type": "string"
            }
          },
          "recipe_step_name": {
            "nullable": true,
            "required": false,
            "schema": {
              "type": "string"
            }
          },
          "recipe_step_no": {
            "nullable": false,
            "required": true,
            "schema": {
              "minimum": 1.0,
              "type": "integer"
            }
          },
          "rule_id": {
            "nullable": false,
            "required": true,
            "schema": {
              "enum": [
                "R01_OOS",
                "R02_OOC",
                "R03_CONSEC"
              ],
              "type": "string"
            }
          },
          "sensor_id": {
            "nullable": false,
            "required": true,
            "schema": {
              "min_length": 1,
              "type": "string"
            }
          },
          "source": {
            "nullable": false,
            "required": true,
            "schema": {
              "enum": [
                "R03",
                "SUMMARY",
                "TRACE"
              ],
              "type": "string"
            }
          },
          "wafer_no": {
            "nullable": true,
            "required": false,
            "schema": {
              "minimum": 1.0,
              "type": "integer"
            }
          }
        },
        "type": "object"
      },
      "shape": "object"
    },
    "422": {
      "schema": {
        "additional_properties": true,
        "fields": {
          "detail": {
            "nullable": false,
            "required": false,
            "schema": {
              "items": {
                "additional_properties": true,
                "fields": {
                  "loc": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "items": {
                        "type": "union",
                        "variants": [
                          {
                            "type": "string"
                          },
                          {
                            "type": "integer"
                          }
                        ]
                      },
                      "type": "array"
                    }
                  },
                  "msg": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "type": "string"
                    }
                  },
                  "type": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "type": "string"
                    }
                  }
                },
                "type": "object"
              },
              "type": "array"
            }
          }
        },
        "type": "object"
      },
      "shape": "object"
    }
  }
}
```

### 4.14 `GET /alarms/paged`

- 구분/담당: 확장 / A
- 요청: query: page>=1,size=1..100 + 필터
- 성공 응답: PageEnvelope<AlarmItem>
- 기타 상태: 422,503
- 정렬·제약: 필수 목록과 같은 정렬
- 호환·경계: /alarms와 응답 shape 혼용 금지
- 계약 규칙:
  - 없음

```json
{
  "request": {
    "body": null,
    "header": {},
    "path": {},
    "query": {
      "area": {
        "nullable": true,
        "required": false,
        "schema": {
          "enum": [
            "ALL",
            "Etch",
            "Photo"
          ],
          "type": "string"
        }
      },
      "chamber_id": {
        "nullable": true,
        "required": false,
        "schema": {
          "type": "string"
        }
      },
      "date": {
        "nullable": true,
        "required": false,
        "schema": {
          "format": "date",
          "type": "string"
        }
      },
      "equipment_id": {
        "nullable": true,
        "required": false,
        "schema": {
          "type": "string"
        }
      },
      "judgement": {
        "nullable": true,
        "required": false,
        "schema": {
          "enum": [
            "OOC",
            "OOS"
          ],
          "type": "string"
        }
      },
      "page": {
        "nullable": false,
        "required": false,
        "schema": {
          "default": 1,
          "minimum": 1,
          "type": "integer"
        }
      },
      "sensor_id": {
        "nullable": true,
        "required": false,
        "schema": {
          "type": "string"
        }
      },
      "size": {
        "nullable": false,
        "required": false,
        "schema": {
          "default": 20,
          "maximum": 100,
          "minimum": 1,
          "type": "integer"
        }
      }
    }
  },
  "responses": {
    "200": {
      "schema": {
        "additional_properties": false,
        "fields": {
          "items": {
            "nullable": false,
            "required": true,
            "schema": {
              "items": {
                "additional_properties": false,
                "fields": {
                  "action_code": {
                    "nullable": true,
                    "required": false,
                    "schema": {
                      "enum": [
                        "EQP_HOLD",
                        "MONITORING",
                        "WARNING"
                      ],
                      "type": "string"
                    }
                  },
                  "action_id": {
                    "nullable": true,
                    "required": false,
                    "schema": {
                      "min_length": 1,
                      "type": "string"
                    }
                  },
                  "agent_run_status": {
                    "nullable": true,
                    "required": false,
                    "schema": {
                      "enum": [
                        "COMPLETED",
                        "FAILED",
                        "RUNNING",
                        "WAITING_APPROVAL"
                      ],
                      "type": "string"
                    }
                  },
                  "alarm_id": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "min_length": 1,
                      "type": "string"
                    }
                  },
                  "approval_status": {
                    "nullable": true,
                    "required": false,
                    "schema": {
                      "enum": [
                        "APPROVED",
                        "AUTO",
                        "EXPIRED",
                        "PENDING",
                        "REJECTED"
                      ],
                      "type": "string"
                    }
                  },
                  "area": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "enum": [
                        "Etch",
                        "Photo"
                      ],
                      "type": "string"
                    }
                  },
                  "chamber_id": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "min_length": 1,
                      "type": "string"
                    }
                  },
                  "detail": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "type": "string"
                    }
                  },
                  "equipment_id": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "min_length": 1,
                      "type": "string"
                    }
                  },
                  "hit_cnt": {
                    "nullable": true,
                    "required": false,
                    "schema": {
                      "minimum": 0.0,
                      "type": "integer"
                    }
                  },
                  "incident": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "additional_properties": false,
                      "fields": {
                        "chamber_id": {
                          "nullable": false,
                          "required": true,
                          "schema": {
                            "min_length": 1,
                            "type": "string"
                          }
                        },
                        "lot_id": {
                          "nullable": false,
                          "required": true,
                          "schema": {
                            "min_length": 1,
                            "type": "string"
                          }
                        }
                      },
                      "type": "object"
                    }
                  },
                  "judgement": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "enum": [
                        "OOC",
                        "OOS"
                      ],
                      "type": "string"
                    }
                  },
                  "latest_agent_run_id": {
                    "nullable": true,
                    "required": false,
                    "schema": {
                      "min_length": 1,
                      "type": "string"
                    }
                  },
                  "lot_hist_id": {
                    "nullable": true,
                    "required": false,
                    "schema": {
                      "min_length": 1,
                      "type": "string"
                    }
                  },
                  "lot_id": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "min_length": 1,
                      "type": "string"
                    }
                  },
                  "occurred_at": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "format": "date-time",
                      "type": "string"
                    }
                  },
                  "recipe_step_name": {
                    "nullable": true,
                    "required": false,
                    "schema": {
                      "type": "string"
                    }
                  },
                  "recipe_step_no": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "minimum": 1.0,
                      "type": "integer"
                    }
                  },
                  "rule_id": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "enum": [
                        "R01_OOS",
                        "R02_OOC",
                        "R03_CONSEC"
                      ],
                      "type": "string"
                    }
                  },
                  "sensor_id": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "min_length": 1,
                      "type": "string"
                    }
                  },
                  "source": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "enum": [
                        "R03",
                        "SUMMARY",
                        "TRACE"
                      ],
                      "type": "string"
                    }
                  },
                  "wafer_no": {
                    "nullable": true,
                    "required": false,
                    "schema": {
                      "minimum": 1.0,
                      "type": "integer"
                    }
                  }
                },
                "type": "object"
              },
              "type": "array"
            }
          },
          "page": {
            "nullable": false,
            "required": true,
            "schema": {
              "minimum": 1.0,
              "type": "integer"
            }
          },
          "size": {
            "nullable": false,
            "required": true,
            "schema": {
              "maximum": 100.0,
              "minimum": 1.0,
              "type": "integer"
            }
          },
          "total": {
            "nullable": false,
            "required": true,
            "schema": {
              "minimum": 0.0,
              "type": "integer"
            }
          }
        },
        "type": "object"
      },
      "shape": "object"
    },
    "422": {
      "schema": {
        "additional_properties": true,
        "fields": {
          "detail": {
            "nullable": false,
            "required": false,
            "schema": {
              "items": {
                "additional_properties": true,
                "fields": {
                  "loc": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "items": {
                        "type": "union",
                        "variants": [
                          {
                            "type": "string"
                          },
                          {
                            "type": "integer"
                          }
                        ]
                      },
                      "type": "array"
                    }
                  },
                  "msg": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "type": "string"
                    }
                  },
                  "type": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "type": "string"
                    }
                  }
                },
                "type": "object"
              },
              "type": "array"
            }
          }
        },
        "type": "object"
      },
      "shape": "object"
    }
  }
}
```

### 4.15 `GET /traces/catalog`

- 구분/담당: 확장 / A
- 요청: query filters
- 성공 응답: TraceCatalogResponse
- 기타 상태: 422,503
- 정렬·제약: 안정 정렬
- 호환·경계: 최소 9개 이후
- 계약 규칙:
  - 없음

```json
{
  "request": {
    "body": null,
    "header": {},
    "path": {},
    "query": {}
  },
  "responses": {
    "200": {
      "schema": {
        "additional_properties": false,
        "fields": {
          "areas": {
            "nullable": false,
            "required": true,
            "schema": {
              "items": {
                "additional_properties": false,
                "fields": {
                  "area_id": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "enum": [
                        "Etch",
                        "Photo"
                      ],
                      "type": "string"
                    }
                  }
                },
                "type": "object"
              },
              "type": "array"
            }
          },
          "equipments": {
            "nullable": false,
            "required": true,
            "schema": {
              "items": {
                "additional_properties": false,
                "fields": {
                  "area_id": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "enum": [
                        "Etch",
                        "Photo"
                      ],
                      "type": "string"
                    }
                  },
                  "chambers": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "items": {
                        "min_length": 1,
                        "type": "string"
                      },
                      "type": "array"
                    }
                  },
                  "equipment_id": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "min_length": 1,
                      "type": "string"
                    }
                  }
                },
                "type": "object"
              },
              "type": "array"
            }
          },
          "lots": {
            "nullable": false,
            "required": true,
            "schema": {
              "items": {
                "additional_properties": false,
                "fields": {
                  "lot_id": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "min_length": 1,
                      "type": "string"
                    }
                  },
                  "wafer_nos": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "items": {
                        "type": "integer"
                      },
                      "type": "array"
                    }
                  }
                },
                "type": "object"
              },
              "type": "array"
            }
          },
          "recipes": {
            "nullable": false,
            "required": true,
            "schema": {
              "items": {
                "additional_properties": false,
                "fields": {
                  "area_id": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "enum": [
                        "Etch",
                        "Photo"
                      ],
                      "type": "string"
                    }
                  },
                  "recipe_id": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "min_length": 1,
                      "type": "string"
                    }
                  }
                },
                "type": "object"
              },
              "type": "array"
            }
          },
          "sensors": {
            "nullable": false,
            "required": true,
            "schema": {
              "items": {
                "additional_properties": false,
                "fields": {
                  "ctrl_lower": {
                    "nullable": true,
                    "required": false,
                    "schema": {
                      "type": "number"
                    }
                  },
                  "ctrl_upper": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "type": "number"
                    }
                  },
                  "sensor_id": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "min_length": 1,
                      "type": "string"
                    }
                  },
                  "sensor_name": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "type": "string"
                    }
                  },
                  "spec_lower": {
                    "nullable": true,
                    "required": false,
                    "schema": {
                      "type": "number"
                    }
                  },
                  "spec_upper": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "type": "number"
                    }
                  },
                  "target": {
                    "nullable": true,
                    "required": false,
                    "schema": {
                      "type": "number"
                    }
                  },
                  "unit": {
                    "nullable": true,
                    "required": false,
                    "schema": {
                      "type": "string"
                    }
                  },
                  "upper_only": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "type": "boolean"
                    }
                  }
                },
                "type": "object"
              },
              "type": "array"
            }
          }
        },
        "type": "object"
      },
      "shape": "object"
    }
  }
}
```

### 4.16 `POST /traces/search`

- 구분/담당: 확장 / A
- 요청: body filters
- 성공 응답: TraceSearchResponse
- 기타 상태: 422,503
- 정렬·제약: 시간순
- 호환·경계: 최소 9개 이후
- 계약 규칙:
  - 없음

```json
{
  "request": {
    "body": {
      "additional_properties": false,
      "fields": {
        "area": {
          "nullable": true,
          "required": false,
          "schema": {
            "enum": [
              "Etch",
              "Photo"
            ],
            "type": "string"
          }
        },
        "chamber_id": {
          "nullable": true,
          "required": false,
          "schema": {
            "min_length": 1,
            "type": "string"
          }
        },
        "equipment_id": {
          "nullable": true,
          "required": false,
          "schema": {
            "min_length": 1,
            "type": "string"
          }
        },
        "from": {
          "nullable": true,
          "required": false,
          "schema": {
            "format": "date-time",
            "type": "string"
          }
        },
        "lot_id": {
          "nullable": true,
          "required": false,
          "schema": {
            "min_length": 1,
            "type": "string"
          }
        },
        "recipe_id": {
          "nullable": true,
          "required": false,
          "schema": {
            "min_length": 1,
            "type": "string"
          }
        },
        "sensor_ids": {
          "nullable": true,
          "required": false,
          "schema": {
            "items": {
              "min_length": 1,
              "type": "string"
            },
            "type": "array"
          }
        },
        "to": {
          "nullable": true,
          "required": false,
          "schema": {
            "format": "date-time",
            "type": "string"
          }
        },
        "wafer_nos": {
          "nullable": true,
          "required": false,
          "schema": {
            "items": {
              "type": "integer"
            },
            "type": "array"
          }
        }
      },
      "type": "object"
    },
    "header": {},
    "path": {},
    "query": {}
  },
  "responses": {
    "200": {
      "schema": {
        "additional_properties": false,
        "fields": {
          "limits": {
            "nullable": false,
            "required": true,
            "schema": {
              "additional_properties": {
                "additional_properties": false,
                "fields": {
                  "ctrl_lower": {
                    "nullable": true,
                    "required": false,
                    "schema": {
                      "type": "number"
                    }
                  },
                  "ctrl_upper": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "type": "number"
                    }
                  },
                  "sensor_id": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "min_length": 1,
                      "type": "string"
                    }
                  },
                  "sensor_name": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "type": "string"
                    }
                  },
                  "spec_lower": {
                    "nullable": true,
                    "required": false,
                    "schema": {
                      "type": "number"
                    }
                  },
                  "spec_upper": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "type": "number"
                    }
                  },
                  "target": {
                    "nullable": true,
                    "required": false,
                    "schema": {
                      "type": "number"
                    }
                  },
                  "unit": {
                    "nullable": true,
                    "required": false,
                    "schema": {
                      "type": "string"
                    }
                  },
                  "upper_only": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "type": "boolean"
                    }
                  }
                },
                "type": "object"
              },
              "fields": {},
              "type": "object"
            }
          },
          "measured_step_stats": {
            "nullable": false,
            "required": false,
            "schema": {
              "additional_properties": {
                "additional_properties": true,
                "fields": {},
                "type": "object"
              },
              "fields": {},
              "type": "object"
            }
          },
          "total": {
            "nullable": false,
            "required": true,
            "schema": {
              "minimum": 0.0,
              "type": "integer"
            }
          },
          "wafers": {
            "nullable": false,
            "required": true,
            "schema": {
              "items": {
                "additional_properties": false,
                "fields": {
                  "chamber_id": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "min_length": 1,
                      "type": "string"
                    }
                  },
                  "equipment_id": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "min_length": 1,
                      "type": "string"
                    }
                  },
                  "lot_hist_id": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "min_length": 1,
                      "type": "string"
                    }
                  },
                  "lot_id": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "min_length": 1,
                      "type": "string"
                    }
                  },
                  "missing_steps": {
                    "nullable": false,
                    "required": false,
                    "schema": {
                      "items": {
                        "type": "string"
                      },
                      "type": "array"
                    }
                  },
                  "occurred_at": {
                    "nullable": true,
                    "required": false,
                    "schema": {
                      "format": "date-time",
                      "type": "string"
                    }
                  },
                  "points": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "items": {
                        "additional_properties": false,
                        "fields": {
                          "measured_at": {
                            "nullable": false,
                            "required": true,
                            "schema": {
                              "format": "date-time",
                              "type": "string"
                            }
                          },
                          "recipe_step_name": {
                            "nullable": true,
                            "required": false,
                            "schema": {
                              "type": "string"
                            }
                          },
                          "recipe_step_no": {
                            "nullable": false,
                            "required": true,
                            "schema": {
                              "minimum": 1.0,
                              "type": "integer"
                            }
                          },
                          "seq_no": {
                            "nullable": false,
                            "required": true,
                            "schema": {
                              "minimum": 0.0,
                              "type": "integer"
                            }
                          },
                          "value": {
                            "nullable": false,
                            "required": true,
                            "schema": {
                              "type": "number"
                            }
                          }
                        },
                        "type": "object"
                      },
                      "type": "array"
                    }
                  },
                  "sensor_id": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "min_length": 1,
                      "type": "string"
                    }
                  },
                  "wafer_no": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "minimum": 1.0,
                      "type": "integer"
                    }
                  }
                },
                "type": "object"
              },
              "type": "array"
            }
          }
        },
        "type": "object"
      },
      "shape": "object"
    },
    "422": {
      "schema": {
        "additional_properties": true,
        "fields": {
          "detail": {
            "nullable": false,
            "required": false,
            "schema": {
              "items": {
                "additional_properties": true,
                "fields": {
                  "loc": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "items": {
                        "type": "union",
                        "variants": [
                          {
                            "type": "string"
                          },
                          {
                            "type": "integer"
                          }
                        ]
                      },
                      "type": "array"
                    }
                  },
                  "msg": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "type": "string"
                    }
                  },
                  "type": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "type": "string"
                    }
                  }
                },
                "type": "object"
              },
              "type": "array"
            }
          }
        },
        "type": "object"
      },
      "shape": "object"
    }
  }
}
```

### 4.17 `GET /relations/equipment/{equipment_id}`

- 구분/담당: 확장 / B
- 요청: path: equipment_id
- 성공 응답: EquipmentRelationsResponse
- 기타 상태: 404,422,503
- 정렬·제약: stable relation_id
- 호환·경계: 고정 설비 상하류 추정 금지
- 계약 규칙:
  - 없음

> deferred inventory: semantic schema는 owner 구현 Task에서 비준한다.

### 4.18 `GET /documents/{document_id}`

- 구분/담당: 확장 / B
- 요청: path: document_id
- 성공 응답: DocumentDetailResponse
- 기타 상태: 404,422,503
- 정렬·제약: chunk order
- 호환·경계: chunk order 안정
- 계약 규칙:
  - 없음

```json
{
  "request": {
    "body": null,
    "header": {},
    "path": {
      "document_id": {
        "nullable": false,
        "required": true,
        "schema": {
          "type": "string"
        }
      }
    },
    "query": {}
  },
  "responses": {
    "200": {
      "schema": {
        "additional_properties": false,
        "fields": {
          "chunks": {
            "nullable": false,
            "required": true,
            "schema": {
              "items": {
                "additional_properties": false,
                "fields": {
                  "chunk_id": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "min_length": 1,
                      "type": "string"
                    }
                  },
                  "chunk_seq": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "minimum": 0.0,
                      "type": "integer"
                    }
                  },
                  "content": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "type": "string"
                    }
                  },
                  "section_title": {
                    "nullable": true,
                    "required": false,
                    "schema": {
                      "type": "string"
                    }
                  }
                },
                "type": "object"
              },
              "type": "array"
            }
          },
          "doc_type": {
            "nullable": true,
            "required": false,
            "schema": {
              "enum": [
                "MANUAL",
                "SPEC",
                "TROUBLESHOOT"
              ],
              "type": "string"
            }
          },
          "document_id": {
            "nullable": false,
            "required": true,
            "schema": {
              "min_length": 1,
              "type": "string"
            }
          },
          "model_code": {
            "nullable": true,
            "required": false,
            "schema": {
              "min_length": 1,
              "type": "string"
            }
          },
          "source_path": {
            "nullable": true,
            "required": false,
            "schema": {
              "type": "string"
            }
          },
          "title": {
            "nullable": false,
            "required": true,
            "schema": {
              "type": "string"
            }
          },
          "version": {
            "nullable": true,
            "required": false,
            "schema": {
              "type": "string"
            }
          }
        },
        "type": "object"
      },
      "shape": "object"
    },
    "422": {
      "schema": {
        "additional_properties": true,
        "fields": {
          "detail": {
            "nullable": false,
            "required": false,
            "schema": {
              "items": {
                "additional_properties": true,
                "fields": {
                  "loc": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "items": {
                        "type": "union",
                        "variants": [
                          {
                            "type": "string"
                          },
                          {
                            "type": "integer"
                          }
                        ]
                      },
                      "type": "array"
                    }
                  },
                  "msg": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "type": "string"
                    }
                  },
                  "type": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "type": "string"
                    }
                  }
                },
                "type": "object"
              },
              "type": "array"
            }
          }
        },
        "type": "object"
      },
      "shape": "object"
    }
  }
}
```

### 4.19 `POST /agent/runs`

- 구분/담당: 실행필수 / C
- 요청: body: alarm={source,alarm_id}
- 성공 응답: 202 AgentRunAccepted
- 기타 상태: 404,409,422,503
- 정렬·제약: 멱등성
- 호환·경계: Alarm 화면 source-aware trigger; legacy /agent/run과 alarm_id 단독 금지; accepted status는 RUNNING이며 body는 agent_run_id+status+alarm 3개
- 계약 규칙:
  - 없음

```json
{
  "request": {
    "body": {
      "additional_properties": false,
      "fields": {
        "alarm": {
          "nullable": false,
          "required": true,
          "schema": {
            "additional_properties": false,
            "fields": {
              "alarm_id": {
                "nullable": false,
                "required": true,
                "schema": {
                  "min_length": 1,
                  "type": "string"
                }
              },
              "source": {
                "nullable": false,
                "required": true,
                "schema": {
                  "enum": [
                    "R03",
                    "SUMMARY",
                    "TRACE"
                  ],
                  "type": "string"
                }
              }
            },
            "type": "object"
          }
        }
      },
      "type": "object"
    },
    "header": {},
    "path": {},
    "query": {}
  },
  "responses": {
    "202": {
      "schema": {
        "additional_properties": false,
        "fields": {
          "agent_run_id": {
            "nullable": false,
            "required": true,
            "schema": {
              "min_length": 1,
              "type": "string"
            }
          },
          "alarm": {
            "nullable": false,
            "required": true,
            "schema": {
              "additional_properties": false,
              "fields": {
                "alarm_id": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "min_length": 1,
                    "type": "string"
                  }
                },
                "source": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "enum": [
                      "R03",
                      "SUMMARY",
                      "TRACE"
                    ],
                    "type": "string"
                  }
                }
              },
              "type": "object"
            }
          },
          "status": {
            "nullable": false,
            "required": true,
            "schema": {
              "type": "string"
            }
          }
        },
        "type": "object"
      },
      "shape": "object"
    },
    "422": {
      "schema": {
        "additional_properties": true,
        "fields": {
          "detail": {
            "nullable": false,
            "required": false,
            "schema": {
              "items": {
                "additional_properties": true,
                "fields": {
                  "loc": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "items": {
                        "type": "union",
                        "variants": [
                          {
                            "type": "string"
                          },
                          {
                            "type": "integer"
                          }
                        ]
                      },
                      "type": "array"
                    }
                  },
                  "msg": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "type": "string"
                    }
                  },
                  "type": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "type": "string"
                    }
                  }
                },
                "type": "object"
              },
              "type": "array"
            }
          }
        },
        "type": "object"
      },
      "shape": "object"
    }
  }
}
```

### 4.20 `GET /agent/runs/{run_id}`

- 구분/담당: 확장 / C
- 요청: path: run_id
- 성공 응답: AgentRunDetailResponse
- 기타 상태: 404,422,503
- 정렬·제약: 단건
- 호환·경계: 근거 ID provenance
- 계약 규칙:
  - 없음

```json
{
  "request": {
    "body": null,
    "header": {},
    "path": {
      "run_id": {
        "nullable": false,
        "required": true,
        "schema": {
          "min_length": 1,
          "type": "string"
        }
      }
    },
    "query": {}
  },
  "responses": {
    "200": {
      "schema": {
        "additional_properties": false,
        "fields": {
          "action": {
            "nullable": true,
            "required": true,
            "schema": {
              "additional_properties": false,
              "fields": {
                "action_code": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "enum": [
                      "EQP_HOLD",
                      "MONITORING",
                      "WARNING"
                    ],
                    "type": "string"
                  }
                },
                "action_id": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "min_length": 1,
                    "type": "string"
                  }
                },
                "agent_run_id": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "min_length": 1,
                    "type": "string"
                  }
                },
                "approval_status": {
                  "nullable": true,
                  "required": true,
                  "schema": {
                    "enum": [
                      "APPROVED",
                      "PENDING",
                      "REJECTED"
                    ],
                    "type": "string"
                  }
                },
                "deliveries": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "items": {
                      "additional_properties": false,
                      "fields": {
                        "channel": {
                          "nullable": false,
                          "required": true,
                          "schema": {
                            "enum": [
                              "EMAIL",
                              "MES"
                            ],
                            "type": "string"
                          }
                        },
                        "status": {
                          "nullable": false,
                          "required": true,
                          "schema": {
                            "enum": [
                              "BLOCKED",
                              "CANCELED",
                              "FAILED",
                              "SENDING",
                              "SENT",
                              "UNKNOWN",
                              "WAITING"
                            ],
                            "type": "string"
                          }
                        }
                      },
                      "type": "object"
                    },
                    "type": "array"
                  }
                },
                "reason": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "min_length": 1,
                    "type": "string"
                  }
                }
              },
              "type": "object"
            }
          },
          "action_id": {
            "nullable": true,
            "required": true,
            "schema": {
              "min_length": 1,
              "type": "string"
            }
          },
          "agent_run_id": {
            "nullable": false,
            "required": true,
            "schema": {
              "min_length": 1,
              "type": "string"
            }
          },
          "alarm_id": {
            "nullable": false,
            "required": true,
            "schema": {
              "min_length": 1,
              "type": "string"
            }
          },
          "alarm_source": {
            "nullable": false,
            "required": true,
            "schema": {
              "enum": [
                "R03",
                "SUMMARY",
                "TRACE"
              ],
              "type": "string"
            }
          },
          "approval": {
            "nullable": true,
            "required": true,
            "schema": {
              "additional_properties": false,
              "fields": {
                "action_id": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "min_length": 1,
                    "type": "string"
                  }
                },
                "agent_run_id": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "min_length": 1,
                    "type": "string"
                  }
                },
                "approval_id": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "min_length": 1,
                    "type": "string"
                  }
                },
                "decided_at": {
                  "nullable": true,
                  "required": true,
                  "schema": {
                    "format": "date-time",
                    "type": "string"
                  }
                },
                "decided_by": {
                  "nullable": true,
                  "required": true,
                  "schema": {
                    "type": "string"
                  }
                },
                "decision_comment": {
                  "nullable": true,
                  "required": true,
                  "schema": {
                    "type": "string"
                  }
                },
                "status": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "enum": [
                      "APPROVED",
                      "PENDING",
                      "REJECTED"
                    ],
                    "type": "string"
                  }
                }
              },
              "type": "object"
            }
          },
          "approval_id": {
            "nullable": true,
            "required": true,
            "schema": {
              "min_length": 1,
              "type": "string"
            }
          },
          "chamber": {
            "nullable": false,
            "required": true,
            "schema": {
              "min_length": 1,
              "type": "string"
            }
          },
          "chamber_id": {
            "nullable": false,
            "required": true,
            "schema": {
              "min_length": 1,
              "type": "string"
            }
          },
          "confidence": {
            "nullable": true,
            "required": true,
            "schema": {
              "maximum": 1.0,
              "minimum": 0.0,
              "type": "number"
            }
          },
          "created_at": {
            "nullable": false,
            "required": true,
            "schema": {
              "format": "date-time",
              "type": "string"
            }
          },
          "deliveries": {
            "nullable": false,
            "required": true,
            "schema": {
              "items": {
                "additional_properties": false,
                "fields": {
                  "channel": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "enum": [
                        "EMAIL",
                        "MES"
                      ],
                      "type": "string"
                    }
                  },
                  "status": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "enum": [
                        "BLOCKED",
                        "CANCELED",
                        "FAILED",
                        "SENDING",
                        "SENT",
                        "UNKNOWN",
                        "WAITING"
                      ],
                      "type": "string"
                    }
                  }
                },
                "type": "object"
              },
              "type": "array"
            }
          },
          "evidence_items": {
            "nullable": false,
            "required": true,
            "schema": {
              "items": {
                "discriminator": "type",
                "type": "discriminated_union",
                "variants": {
                  "ALARM": {
                    "additional_properties": false,
                    "fields": {
                      "alarm": {
                        "nullable": false,
                        "required": true,
                        "schema": {
                          "additional_properties": false,
                          "fields": {
                            "alarm_id": {
                              "nullable": false,
                              "required": true,
                              "schema": {
                                "min_length": 1,
                                "type": "string"
                              }
                            },
                            "source": {
                              "nullable": false,
                              "required": true,
                              "schema": {
                                "enum": [
                                  "R03",
                                  "SUMMARY",
                                  "TRACE"
                                ],
                                "type": "string"
                              }
                            }
                          },
                          "type": "object"
                        }
                      },
                      "excerpt": {
                        "nullable": false,
                        "required": true,
                        "schema": {
                          "min_length": 1,
                          "type": "string"
                        }
                      },
                      "source_id": {
                        "nullable": false,
                        "required": true,
                        "schema": {
                          "min_length": 1,
                          "type": "string"
                        }
                      },
                      "title": {
                        "nullable": false,
                        "required": true,
                        "schema": {
                          "min_length": 1,
                          "type": "string"
                        }
                      },
                      "type": {
                        "nullable": false,
                        "required": true,
                        "schema": {
                          "type": "string"
                        }
                      }
                    },
                    "type": "object"
                  },
                  "DOCUMENT": {
                    "additional_properties": false,
                    "fields": {
                      "chunk_id": {
                        "nullable": false,
                        "required": true,
                        "schema": {
                          "min_length": 1,
                          "type": "string"
                        }
                      },
                      "document_id": {
                        "nullable": false,
                        "required": true,
                        "schema": {
                          "min_length": 1,
                          "type": "string"
                        }
                      },
                      "excerpt": {
                        "nullable": false,
                        "required": true,
                        "schema": {
                          "min_length": 1,
                          "type": "string"
                        }
                      },
                      "section": {
                        "nullable": true,
                        "required": true,
                        "schema": {
                          "type": "string"
                        }
                      },
                      "source_id": {
                        "nullable": false,
                        "required": true,
                        "schema": {
                          "min_length": 1,
                          "type": "string"
                        }
                      },
                      "title": {
                        "nullable": false,
                        "required": true,
                        "schema": {
                          "min_length": 1,
                          "type": "string"
                        }
                      },
                      "type": {
                        "nullable": false,
                        "required": true,
                        "schema": {
                          "type": "string"
                        }
                      }
                    },
                    "type": "object"
                  },
                  "GRAPH": {
                    "additional_properties": false,
                    "fields": {
                      "excerpt": {
                        "nullable": false,
                        "required": true,
                        "schema": {
                          "min_length": 1,
                          "type": "string"
                        }
                      },
                      "graph_revision": {
                        "nullable": false,
                        "required": true,
                        "schema": {
                          "min_length": 1,
                          "type": "string"
                        }
                      },
                      "relation_id": {
                        "nullable": false,
                        "required": true,
                        "schema": {
                          "min_length": 1,
                          "type": "string"
                        }
                      },
                      "source_id": {
                        "nullable": false,
                        "required": true,
                        "schema": {
                          "min_length": 1,
                          "type": "string"
                        }
                      },
                      "title": {
                        "nullable": false,
                        "required": true,
                        "schema": {
                          "min_length": 1,
                          "type": "string"
                        }
                      },
                      "type": {
                        "nullable": false,
                        "required": true,
                        "schema": {
                          "type": "string"
                        }
                      }
                    },
                    "type": "object"
                  },
                  "METROLOGY": {
                    "additional_properties": false,
                    "fields": {
                      "excerpt": {
                        "nullable": false,
                        "required": true,
                        "schema": {
                          "min_length": 1,
                          "type": "string"
                        }
                      },
                      "source_id": {
                        "nullable": false,
                        "required": true,
                        "schema": {
                          "min_length": 1,
                          "type": "string"
                        }
                      },
                      "title": {
                        "nullable": false,
                        "required": true,
                        "schema": {
                          "min_length": 1,
                          "type": "string"
                        }
                      },
                      "type": {
                        "nullable": false,
                        "required": true,
                        "schema": {
                          "type": "string"
                        }
                      }
                    },
                    "type": "object"
                  },
                  "TRACE": {
                    "additional_properties": false,
                    "fields": {
                      "excerpt": {
                        "nullable": false,
                        "required": true,
                        "schema": {
                          "min_length": 1,
                          "type": "string"
                        }
                      },
                      "source_id": {
                        "nullable": false,
                        "required": true,
                        "schema": {
                          "min_length": 1,
                          "type": "string"
                        }
                      },
                      "title": {
                        "nullable": false,
                        "required": true,
                        "schema": {
                          "min_length": 1,
                          "type": "string"
                        }
                      },
                      "type": {
                        "nullable": false,
                        "required": true,
                        "schema": {
                          "type": "string"
                        }
                      }
                    },
                    "type": "object"
                  }
                }
              },
              "type": "array"
            }
          },
          "fault_code": {
            "nullable": true,
            "required": true,
            "schema": {
              "enum": [
                "FOC",
                "MFD",
                "OTH",
                "RFM",
                "TMD"
              ],
              "type": "string"
            }
          },
          "fault_color": {
            "nullable": false,
            "required": true,
            "schema": {
              "type": "null"
            }
          },
          "fault_name": {
            "nullable": false,
            "required": true,
            "schema": {
              "type": "null"
            }
          },
          "latency_ms": {
            "nullable": false,
            "required": true,
            "schema": {
              "minimum": 0.0,
              "type": "integer"
            }
          },
          "llm_model": {
            "nullable": false,
            "required": true,
            "schema": {
              "min_length": 1,
              "type": "string"
            }
          },
          "predicted_fault_code": {
            "nullable": true,
            "required": true,
            "schema": {
              "enum": [
                "FOC",
                "MFD",
                "OTH",
                "RFM",
                "TMD"
              ],
              "type": "string"
            }
          },
          "recommended_action": {
            "nullable": true,
            "required": true,
            "schema": {
              "enum": [
                "EQP_HOLD",
                "MONITORING",
                "WARNING"
              ],
              "type": "string"
            }
          },
          "status": {
            "nullable": false,
            "required": true,
            "schema": {
              "enum": [
                "COMPLETED",
                "FAILED",
                "RUNNING",
                "WAITING_APPROVAL"
              ],
              "type": "string"
            }
          },
          "tools": {
            "nullable": false,
            "required": true,
            "schema": {
              "items": {
                "additional_properties": false,
                "fields": {
                  "n": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "min_length": 1,
                      "type": "string"
                    }
                  },
                  "result_summary": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "min_length": 1,
                      "type": "string"
                    }
                  },
                  "s": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "enum": [
                        "ERROR",
                        "SUCCESS",
                        "TIMEOUT"
                      ],
                      "type": "string"
                    }
                  },
                  "status": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "enum": [
                        "ERROR",
                        "SUCCESS",
                        "TIMEOUT"
                      ],
                      "type": "string"
                    }
                  },
                  "tool_name": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "min_length": 1,
                      "type": "string"
                    }
                  }
                },
                "type": "object"
              },
              "type": "array"
            }
          }
        },
        "type": "object"
      },
      "shape": "object"
    },
    "404": {
      "schema": {
        "additional_properties": false,
        "fields": {
          "code": {
            "nullable": false,
            "required": true,
            "schema": {
              "enum": [
                "APPROVAL_ALREADY_DECIDED",
                "DEPENDENCY_NOT_READY",
                "IDEMPOTENCY_CONFLICT",
                "INCIDENT_ALREADY_PROCESSED",
                "INCIDENT_ALREADY_RUNNING",
                "INTERNAL_ERROR",
                "LEGACY_APPROVAL_NOT_LINKED",
                "LLM_NOT_READY",
                "MODEL_NOT_READY",
                "POLICY_REJECTED",
                "RESOURCE_NOT_FOUND",
                "UNAUTHORIZED",
                "VALIDATION_ERROR"
              ],
              "type": "string"
            }
          },
          "details": {
            "nullable": false,
            "required": false,
            "schema": {
              "additional_properties": true,
              "fields": {},
              "type": "object"
            }
          },
          "message": {
            "nullable": false,
            "required": true,
            "schema": {
              "type": "string"
            }
          }
        },
        "type": "object"
      },
      "shape": "object"
    },
    "422": {
      "schema": {
        "additional_properties": false,
        "fields": {
          "code": {
            "nullable": false,
            "required": true,
            "schema": {
              "enum": [
                "APPROVAL_ALREADY_DECIDED",
                "DEPENDENCY_NOT_READY",
                "IDEMPOTENCY_CONFLICT",
                "INCIDENT_ALREADY_PROCESSED",
                "INCIDENT_ALREADY_RUNNING",
                "INTERNAL_ERROR",
                "LEGACY_APPROVAL_NOT_LINKED",
                "LLM_NOT_READY",
                "MODEL_NOT_READY",
                "POLICY_REJECTED",
                "RESOURCE_NOT_FOUND",
                "UNAUTHORIZED",
                "VALIDATION_ERROR"
              ],
              "type": "string"
            }
          },
          "details": {
            "nullable": false,
            "required": false,
            "schema": {
              "additional_properties": true,
              "fields": {},
              "type": "object"
            }
          },
          "message": {
            "nullable": false,
            "required": true,
            "schema": {
              "type": "string"
            }
          }
        },
        "type": "object"
      },
      "shape": "object"
    },
    "503": {
      "schema": {
        "additional_properties": false,
        "fields": {
          "code": {
            "nullable": false,
            "required": true,
            "schema": {
              "enum": [
                "APPROVAL_ALREADY_DECIDED",
                "DEPENDENCY_NOT_READY",
                "IDEMPOTENCY_CONFLICT",
                "INCIDENT_ALREADY_PROCESSED",
                "INCIDENT_ALREADY_RUNNING",
                "INTERNAL_ERROR",
                "LEGACY_APPROVAL_NOT_LINKED",
                "LLM_NOT_READY",
                "MODEL_NOT_READY",
                "POLICY_REJECTED",
                "RESOURCE_NOT_FOUND",
                "UNAUTHORIZED",
                "VALIDATION_ERROR"
              ],
              "type": "string"
            }
          },
          "details": {
            "nullable": false,
            "required": false,
            "schema": {
              "additional_properties": true,
              "fields": {},
              "type": "object"
            }
          },
          "message": {
            "nullable": false,
            "required": true,
            "schema": {
              "type": "string"
            }
          }
        },
        "type": "object"
      },
      "shape": "object"
    }
  }
}
```

### 4.21 `POST /agent/runs/{run_id}/retry`

- 구분/담당: 확장 / C
- 요청: path: run_id
- 성공 응답: 202 AgentRunAccepted
- 기타 상태: 404,409,422,503
- 정렬·제약: 재시도 정책
- 호환·경계: 성공·실행중 재시도 거부
- 계약 규칙:
  - 없음

> deferred inventory: semantic schema는 owner 구현 Task에서 비준한다.

### 4.22 `GET /agent/runs/paged`

- 구분/담당: 확장 / C
- 요청: query: page,size,filters
- 성공 응답: PageEnvelope<AgentRunItem>
- 기타 상태: 422,503
- 정렬·제약: created_at DESC; agent_run_id DESC
- 호환·경계: /agent/runs와 shape 혼용 금지
- 계약 규칙:
  - 없음

> deferred inventory: semantic schema는 owner 구현 Task에서 비준한다.

### 4.23 `GET /approvals/paged`

- 구분/담당: 확장 / C
- 요청: query: page,size,filters
- 성공 응답: PageEnvelope<ApprovalItem>
- 기타 상태: 422,503
- 정렬·제약: created_at DESC; approval_id DESC
- 호환·경계: /approvals와 shape 혼용 금지
- 계약 규칙:
  - 없음

> deferred inventory: semantic schema는 owner 구현 Task에서 비준한다.

### 4.24 `GET /actions`

- 구분/담당: 확장 / C
- 요청: query filters
- 성공 응답: ActionItem[]
- 기타 상태: 422,503
- 정렬·제약: created_at DESC
- 호환·경계: delivery summary 포함
- 계약 규칙:
  - 없음

```json
{
  "request": {
    "body": null,
    "header": {},
    "path": {},
    "query": {
      "action_code": {
        "nullable": true,
        "required": false,
        "schema": {
          "enum": [
            "EQP_HOLD",
            "MONITORING",
            "WARNING"
          ],
          "type": "string"
        }
      }
    }
  },
  "responses": {
    "200": {
      "schema": {
        "items": {
          "additional_properties": false,
          "fields": {
            "action_code": {
              "nullable": false,
              "required": true,
              "schema": {
                "enum": [
                  "EQP_HOLD",
                  "MONITORING",
                  "WARNING"
                ],
                "type": "string"
              }
            },
            "action_id": {
              "nullable": false,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            },
            "agent_run_id": {
              "nullable": false,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            },
            "approval_status": {
              "nullable": true,
              "required": true,
              "schema": {
                "enum": [
                  "APPROVED",
                  "PENDING",
                  "REJECTED"
                ],
                "type": "string"
              }
            },
            "chamber": {
              "nullable": false,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            },
            "chamber_id": {
              "nullable": false,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            },
            "created_at": {
              "nullable": false,
              "required": true,
              "schema": {
                "format": "date-time",
                "type": "string"
              }
            },
            "created_by_agent_run_id": {
              "nullable": false,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            },
            "deliveries": {
              "nullable": false,
              "required": true,
              "schema": {
                "items": {
                  "additional_properties": false,
                  "fields": {
                    "channel": {
                      "nullable": false,
                      "required": true,
                      "schema": {
                        "enum": [
                          "EMAIL",
                          "MES"
                        ],
                        "type": "string"
                      }
                    },
                    "status": {
                      "nullable": false,
                      "required": true,
                      "schema": {
                        "enum": [
                          "BLOCKED",
                          "CANCELED",
                          "FAILED",
                          "SENDING",
                          "SENT",
                          "UNKNOWN",
                          "WAITING"
                        ],
                        "type": "string"
                      }
                    }
                  },
                  "type": "object"
                },
                "type": "array"
              }
            },
            "equipment": {
              "nullable": true,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            },
            "equipment_id": {
              "nullable": true,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            },
            "lot": {
              "nullable": false,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            },
            "lot_id": {
              "nullable": false,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            },
            "reason": {
              "nullable": false,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            }
          },
          "type": "object"
        },
        "type": "array"
      },
      "shape": "array"
    },
    "422": {
      "schema": {
        "additional_properties": false,
        "fields": {
          "code": {
            "nullable": false,
            "required": true,
            "schema": {
              "enum": [
                "APPROVAL_ALREADY_DECIDED",
                "DEPENDENCY_NOT_READY",
                "IDEMPOTENCY_CONFLICT",
                "INCIDENT_ALREADY_PROCESSED",
                "INCIDENT_ALREADY_RUNNING",
                "INTERNAL_ERROR",
                "LEGACY_APPROVAL_NOT_LINKED",
                "LLM_NOT_READY",
                "MODEL_NOT_READY",
                "POLICY_REJECTED",
                "RESOURCE_NOT_FOUND",
                "UNAUTHORIZED",
                "VALIDATION_ERROR"
              ],
              "type": "string"
            }
          },
          "details": {
            "nullable": false,
            "required": false,
            "schema": {
              "additional_properties": true,
              "fields": {},
              "type": "object"
            }
          },
          "message": {
            "nullable": false,
            "required": true,
            "schema": {
              "type": "string"
            }
          }
        },
        "type": "object"
      },
      "shape": "object"
    },
    "503": {
      "schema": {
        "additional_properties": false,
        "fields": {
          "code": {
            "nullable": false,
            "required": true,
            "schema": {
              "enum": [
                "APPROVAL_ALREADY_DECIDED",
                "DEPENDENCY_NOT_READY",
                "IDEMPOTENCY_CONFLICT",
                "INCIDENT_ALREADY_PROCESSED",
                "INCIDENT_ALREADY_RUNNING",
                "INTERNAL_ERROR",
                "LEGACY_APPROVAL_NOT_LINKED",
                "LLM_NOT_READY",
                "MODEL_NOT_READY",
                "POLICY_REJECTED",
                "RESOURCE_NOT_FOUND",
                "UNAUTHORIZED",
                "VALIDATION_ERROR"
              ],
              "type": "string"
            }
          },
          "details": {
            "nullable": false,
            "required": false,
            "schema": {
              "additional_properties": true,
              "fields": {},
              "type": "object"
            }
          },
          "message": {
            "nullable": false,
            "required": true,
            "schema": {
              "type": "string"
            }
          }
        },
        "type": "object"
      },
      "shape": "object"
    }
  }
}
```

### 4.25 `GET /actions/{action_id}`

- 구분/담당: 확장 / C
- 요청: path: action_id
- 성공 응답: ActionDetailResponse
- 기타 상태: 404,422,503
- 정렬·제약: 단건
- 호환·경계: channel별 상태
- 계약 규칙:
  - 없음

```json
{
  "request": {
    "body": null,
    "header": {},
    "path": {
      "action_id": {
        "nullable": false,
        "required": true,
        "schema": {
          "min_length": 1,
          "type": "string"
        }
      }
    },
    "query": {}
  },
  "responses": {
    "200": {
      "schema": {
        "additional_properties": false,
        "fields": {
          "action_code": {
            "nullable": false,
            "required": true,
            "schema": {
              "enum": [
                "EQP_HOLD",
                "MONITORING",
                "WARNING"
              ],
              "type": "string"
            }
          },
          "action_id": {
            "nullable": false,
            "required": true,
            "schema": {
              "min_length": 1,
              "type": "string"
            }
          },
          "agent_run_id": {
            "nullable": false,
            "required": true,
            "schema": {
              "min_length": 1,
              "type": "string"
            }
          },
          "approval_status": {
            "nullable": true,
            "required": true,
            "schema": {
              "enum": [
                "APPROVED",
                "PENDING",
                "REJECTED"
              ],
              "type": "string"
            }
          },
          "chamber": {
            "nullable": false,
            "required": true,
            "schema": {
              "min_length": 1,
              "type": "string"
            }
          },
          "chamber_id": {
            "nullable": false,
            "required": true,
            "schema": {
              "min_length": 1,
              "type": "string"
            }
          },
          "created_at": {
            "nullable": false,
            "required": true,
            "schema": {
              "format": "date-time",
              "type": "string"
            }
          },
          "created_by_agent_run_id": {
            "nullable": false,
            "required": true,
            "schema": {
              "min_length": 1,
              "type": "string"
            }
          },
          "deliveries": {
            "nullable": false,
            "required": true,
            "schema": {
              "items": {
                "additional_properties": false,
                "fields": {
                  "channel": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "enum": [
                        "EMAIL",
                        "MES"
                      ],
                      "type": "string"
                    }
                  },
                  "completed_at": {
                    "nullable": true,
                    "required": true,
                    "schema": {
                      "format": "date-time",
                      "type": "string"
                    }
                  },
                  "started_at": {
                    "nullable": true,
                    "required": true,
                    "schema": {
                      "format": "date-time",
                      "type": "string"
                    }
                  },
                  "status": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "enum": [
                        "BLOCKED",
                        "CANCELED",
                        "FAILED",
                        "SENDING",
                        "SENT",
                        "UNKNOWN",
                        "WAITING"
                      ],
                      "type": "string"
                    }
                  }
                },
                "type": "object"
              },
              "type": "array"
            }
          },
          "equipment": {
            "nullable": true,
            "required": true,
            "schema": {
              "min_length": 1,
              "type": "string"
            }
          },
          "equipment_id": {
            "nullable": true,
            "required": true,
            "schema": {
              "min_length": 1,
              "type": "string"
            }
          },
          "lot": {
            "nullable": false,
            "required": true,
            "schema": {
              "min_length": 1,
              "type": "string"
            }
          },
          "lot_id": {
            "nullable": false,
            "required": true,
            "schema": {
              "min_length": 1,
              "type": "string"
            }
          },
          "reason": {
            "nullable": false,
            "required": true,
            "schema": {
              "min_length": 1,
              "type": "string"
            }
          }
        },
        "type": "object"
      },
      "shape": "object"
    },
    "404": {
      "schema": {
        "additional_properties": false,
        "fields": {
          "code": {
            "nullable": false,
            "required": true,
            "schema": {
              "enum": [
                "APPROVAL_ALREADY_DECIDED",
                "DEPENDENCY_NOT_READY",
                "IDEMPOTENCY_CONFLICT",
                "INCIDENT_ALREADY_PROCESSED",
                "INCIDENT_ALREADY_RUNNING",
                "INTERNAL_ERROR",
                "LEGACY_APPROVAL_NOT_LINKED",
                "LLM_NOT_READY",
                "MODEL_NOT_READY",
                "POLICY_REJECTED",
                "RESOURCE_NOT_FOUND",
                "UNAUTHORIZED",
                "VALIDATION_ERROR"
              ],
              "type": "string"
            }
          },
          "details": {
            "nullable": false,
            "required": false,
            "schema": {
              "additional_properties": true,
              "fields": {},
              "type": "object"
            }
          },
          "message": {
            "nullable": false,
            "required": true,
            "schema": {
              "type": "string"
            }
          }
        },
        "type": "object"
      },
      "shape": "object"
    },
    "422": {
      "schema": {
        "additional_properties": false,
        "fields": {
          "code": {
            "nullable": false,
            "required": true,
            "schema": {
              "enum": [
                "APPROVAL_ALREADY_DECIDED",
                "DEPENDENCY_NOT_READY",
                "IDEMPOTENCY_CONFLICT",
                "INCIDENT_ALREADY_PROCESSED",
                "INCIDENT_ALREADY_RUNNING",
                "INTERNAL_ERROR",
                "LEGACY_APPROVAL_NOT_LINKED",
                "LLM_NOT_READY",
                "MODEL_NOT_READY",
                "POLICY_REJECTED",
                "RESOURCE_NOT_FOUND",
                "UNAUTHORIZED",
                "VALIDATION_ERROR"
              ],
              "type": "string"
            }
          },
          "details": {
            "nullable": false,
            "required": false,
            "schema": {
              "additional_properties": true,
              "fields": {},
              "type": "object"
            }
          },
          "message": {
            "nullable": false,
            "required": true,
            "schema": {
              "type": "string"
            }
          }
        },
        "type": "object"
      },
      "shape": "object"
    },
    "503": {
      "schema": {
        "additional_properties": false,
        "fields": {
          "code": {
            "nullable": false,
            "required": true,
            "schema": {
              "enum": [
                "APPROVAL_ALREADY_DECIDED",
                "DEPENDENCY_NOT_READY",
                "IDEMPOTENCY_CONFLICT",
                "INCIDENT_ALREADY_PROCESSED",
                "INCIDENT_ALREADY_RUNNING",
                "INTERNAL_ERROR",
                "LEGACY_APPROVAL_NOT_LINKED",
                "LLM_NOT_READY",
                "MODEL_NOT_READY",
                "POLICY_REJECTED",
                "RESOURCE_NOT_FOUND",
                "UNAUTHORIZED",
                "VALIDATION_ERROR"
              ],
              "type": "string"
            }
          },
          "details": {
            "nullable": false,
            "required": false,
            "schema": {
              "additional_properties": true,
              "fields": {},
              "type": "object"
            }
          },
          "message": {
            "nullable": false,
            "required": true,
            "schema": {
              "type": "string"
            }
          }
        },
        "type": "object"
      },
      "shape": "object"
    }
  }
}
```

### 4.26 `POST /actions/{action_id}/deliveries/{channel}/retry`

- 구분/담당: 확장 / C
- 요청: path: action_id; channel=EMAIL|MES
- 성공 응답: PublicDeliveryResult
- 기타 상태: 404,409,422,503
- 정렬·제약: 멱등성
- 호환·경계: DeliveryResult와 같은 field; public MES는 내부 MES_MOCK의 projection
- 계약 규칙:
  - 없음

> deferred inventory: semantic schema는 owner 구현 Task에서 비준한다.

### 4.27 `POST /internal/actions/{action_id}/delivery`

- 구분/담당: 내부 / C
- 요청: HMAC timestamp/signature; body: event_id,channel=EMAIL|MES_MOCK,status=SENT|FAILED,provider_message_id,request_hash,completed_at,error_code
- 성공 응답: DeliveryResult
- 기타 상태: 401,404,409,422,503
- 정렬·제약: action+channel+request_hash 멱등
- 호환·경계: n8n·Kafka worker 전용; replay window 300초
- 계약 규칙:
  - FAILED requires error_code non-null and permits provider_message_id null
  - SENT requires provider_message_id non-null and error_code null

```json
{
  "request": {
    "body": {
      "additional_properties": false,
      "fields": {
        "channel": {
          "nullable": false,
          "required": true,
          "schema": {
            "enum": [
              "EMAIL",
              "MES_MOCK"
            ],
            "type": "string"
          }
        },
        "completed_at": {
          "nullable": false,
          "required": true,
          "schema": {
            "format": "date-time",
            "type": "string"
          }
        },
        "error_code": {
          "nullable": true,
          "required": true,
          "schema": {
            "min_length": 1,
            "type": "string"
          }
        },
        "event_id": {
          "nullable": false,
          "required": true,
          "schema": {
            "min_length": 1,
            "type": "string"
          }
        },
        "provider_message_id": {
          "nullable": true,
          "required": true,
          "schema": {
            "min_length": 1,
            "type": "string"
          }
        },
        "request_hash": {
          "nullable": false,
          "required": true,
          "schema": {
            "max_length": 64,
            "min_length": 64,
            "pattern": "^[0-9a-f]{64}$",
            "type": "string"
          }
        },
        "status": {
          "nullable": false,
          "required": true,
          "schema": {
            "enum": [
              "FAILED",
              "SENT"
            ],
            "type": "string"
          }
        }
      },
      "type": "object"
    },
    "header": {
      "X-Delivery-Signature": {
        "nullable": true,
        "required": false,
        "schema": {
          "type": "string"
        }
      },
      "X-Delivery-Timestamp": {
        "nullable": true,
        "required": false,
        "schema": {
          "type": "string"
        }
      }
    },
    "path": {
      "action_id": {
        "nullable": false,
        "required": true,
        "schema": {
          "type": "string"
        }
      }
    },
    "query": {}
  },
  "responses": {
    "200": {
      "schema": {
        "additional_properties": false,
        "fields": {
          "action_id": {
            "nullable": false,
            "required": true,
            "schema": {
              "max_length": 20,
              "min_length": 1,
              "type": "string"
            }
          },
          "channel": {
            "nullable": false,
            "required": true,
            "schema": {
              "enum": [
                "EMAIL",
                "MES_MOCK"
              ],
              "type": "string"
            }
          },
          "completed_at": {
            "nullable": false,
            "required": true,
            "schema": {
              "format": "date-time",
              "type": "string"
            }
          },
          "duplicate": {
            "nullable": false,
            "required": true,
            "schema": {
              "type": "boolean"
            }
          },
          "error_code": {
            "nullable": true,
            "required": true,
            "schema": {
              "min_length": 1,
              "type": "string"
            }
          },
          "provider_message_id": {
            "nullable": true,
            "required": true,
            "schema": {
              "min_length": 1,
              "type": "string"
            }
          },
          "request_hash": {
            "nullable": false,
            "required": true,
            "schema": {
              "max_length": 64,
              "min_length": 64,
              "pattern": "^[0-9a-f]{64}$",
              "type": "string"
            }
          },
          "status": {
            "nullable": false,
            "required": true,
            "schema": {
              "enum": [
                "FAILED",
                "SENT"
              ],
              "type": "string"
            }
          }
        },
        "type": "object"
      },
      "shape": "object"
    },
    "422": {
      "schema": {
        "additional_properties": true,
        "fields": {
          "detail": {
            "nullable": false,
            "required": false,
            "schema": {
              "items": {
                "additional_properties": true,
                "fields": {
                  "loc": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "items": {
                        "type": "union",
                        "variants": [
                          {
                            "type": "string"
                          },
                          {
                            "type": "integer"
                          }
                        ]
                      },
                      "type": "array"
                    }
                  },
                  "msg": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "type": "string"
                    }
                  },
                  "type": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "type": "string"
                    }
                  }
                },
                "type": "object"
              },
              "type": "array"
            }
          }
        },
        "type": "object"
      },
      "shape": "object"
    }
  }
}
```

### 4.28 `POST /analytics/query`

- 구분/담당: 팀필수 / D
- 요청: body: question 1..1000
- 성공 응답: AnalysisQueryResponse
- 기타 상태: 422,503
- 정렬·제약: read-only SELECT; row limit
- 호환·경계: 7화면 release 필수; 정책 거부는 200+is_rejected=true; 합성 GT 제외
- 계약 규칙:
  - 없음

```json
{
  "request": {
    "body": {
      "additional_properties": false,
      "fields": {
        "question": {
          "nullable": false,
          "required": true,
          "schema": {
            "max_length": 1000,
            "min_length": 1,
            "type": "string"
          }
        }
      },
      "type": "object"
    },
    "header": {},
    "path": {},
    "query": {}
  },
  "responses": {
    "200": {
      "schema": {
        "additional_properties": false,
        "fields": {
          "columns": {
            "nullable": false,
            "required": true,
            "schema": {
              "items": {
                "type": "string"
              },
              "type": "array"
            }
          },
          "cross_check": {
            "nullable": true,
            "required": false,
            "schema": {
              "additional_properties": false,
              "fields": {
                "cypher": {
                  "nullable": true,
                  "required": false,
                  "schema": {
                    "type": "string"
                  }
                },
                "status": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "enum": [
                      "MATCH",
                      "MISMATCH",
                      "SKIPPED"
                    ],
                    "type": "string"
                  }
                },
                "summary": {
                  "nullable": true,
                  "required": false,
                  "schema": {
                    "type": "string"
                  }
                }
              },
              "type": "object"
            }
          },
          "error_msg": {
            "nullable": true,
            "required": false,
            "schema": {
              "type": "string"
            }
          },
          "generated_sql": {
            "nullable": true,
            "required": false,
            "schema": {
              "type": "string"
            }
          },
          "group_by": {
            "nullable": false,
            "required": true,
            "schema": {
              "items": {
                "type": "string"
              },
              "type": "array"
            }
          },
          "is_rejected": {
            "nullable": false,
            "required": true,
            "schema": {
              "type": "boolean"
            }
          },
          "is_valid": {
            "nullable": false,
            "required": true,
            "schema": {
              "type": "boolean"
            }
          },
          "latency_ms": {
            "nullable": false,
            "required": true,
            "schema": {
              "minimum": 0.0,
              "type": "integer"
            }
          },
          "metric": {
            "nullable": true,
            "required": false,
            "schema": {
              "additional_properties": false,
              "fields": {
                "column": {
                  "nullable": true,
                  "required": false,
                  "schema": {
                    "type": "string"
                  }
                },
                "p": {
                  "nullable": true,
                  "required": false,
                  "schema": {
                    "maximum": 100.0,
                    "minimum": 0.0,
                    "type": "number"
                  }
                },
                "type": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "enum": [
                      "count",
                      "max",
                      "mean",
                      "median",
                      "min",
                      "percentile",
                      "ratio",
                      "std",
                      "sum"
                    ],
                    "type": "string"
                  }
                }
              },
              "type": "object"
            }
          },
          "metric_result": {
            "nullable": false,
            "required": false,
            "schema": {
              "type": "union",
              "variants": [
                {
                  "type": "integer"
                },
                {
                  "type": "number"
                },
                {
                  "items": {
                    "additional_properties": false,
                    "fields": {
                      "group": {
                        "nullable": false,
                        "required": true,
                        "schema": {
                          "additional_properties": true,
                          "fields": {},
                          "type": "object"
                        }
                      },
                      "value": {
                        "nullable": false,
                        "required": false,
                        "schema": {
                          "type": "union",
                          "variants": [
                            {
                              "type": "integer"
                            },
                            {
                              "type": "number"
                            },
                            {
                              "type": "null"
                            }
                          ]
                        }
                      }
                    },
                    "type": "object"
                  },
                  "type": "array"
                },
                {
                  "type": "null"
                }
              ]
            }
          },
          "nl_query_log_id": {
            "nullable": true,
            "required": false,
            "schema": {
              "minimum": 1.0,
              "type": "integer"
            }
          },
          "question": {
            "nullable": false,
            "required": true,
            "schema": {
              "type": "string"
            }
          },
          "reject_reason": {
            "nullable": true,
            "required": false,
            "schema": {
              "type": "string"
            }
          },
          "row_count": {
            "nullable": false,
            "required": true,
            "schema": {
              "minimum": 0.0,
              "type": "integer"
            }
          },
          "rows": {
            "nullable": false,
            "required": true,
            "schema": {
              "items": {
                "additional_properties": true,
                "fields": {},
                "type": "object"
              },
              "type": "array"
            }
          },
          "visualization": {
            "nullable": true,
            "required": false,
            "schema": {
              "additional_properties": false,
              "fields": {
                "chart_type": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "enum": [
                      "bar",
                      "histogram",
                      "line",
                      "table"
                    ],
                    "type": "string"
                  }
                },
                "x": {
                  "nullable": true,
                  "required": false,
                  "schema": {
                    "type": "string"
                  }
                },
                "y": {
                  "nullable": true,
                  "required": false,
                  "schema": {
                    "type": "string"
                  }
                }
              },
              "type": "object"
            }
          }
        },
        "type": "object"
      },
      "shape": "object"
    },
    "422": {
      "schema": {
        "additional_properties": true,
        "fields": {
          "detail": {
            "nullable": false,
            "required": false,
            "schema": {
              "items": {
                "additional_properties": true,
                "fields": {
                  "loc": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "items": {
                        "type": "union",
                        "variants": [
                          {
                            "type": "string"
                          },
                          {
                            "type": "integer"
                          }
                        ]
                      },
                      "type": "array"
                    }
                  },
                  "msg": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "type": "string"
                    }
                  },
                  "type": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "type": "string"
                    }
                  }
                },
                "type": "object"
              },
              "type": "array"
            }
          }
        },
        "type": "object"
      },
      "shape": "object"
    }
  }
}
```

### 4.29 `POST /analytics/graph-query`

- 구분/담당: 확장 / D
- 요청: body: question 1..1000
- 성공 응답: GraphQueryResponse
- 기타 상태: 422
- 정렬·제약: validated read-only Cypher; row limit
- 호환·경계: backend 전용 plan·validator 경로; 사용자 Cypher passthrough 없음
- 계약 규칙:
  - 없음

```json
{
  "request": {
    "body": {
      "additional_properties": false,
      "fields": {
        "question": {
          "nullable": false,
          "required": true,
          "schema": {
            "max_length": 1000,
            "min_length": 1,
            "type": "string"
          }
        }
      },
      "type": "object"
    },
    "header": {},
    "path": {},
    "query": {}
  },
  "responses": {
    "200": {
      "schema": {
        "additional_properties": false,
        "fields": {
          "columns": {
            "nullable": false,
            "required": true,
            "schema": {
              "items": {
                "type": "string"
              },
              "type": "array"
            }
          },
          "error_msg": {
            "nullable": true,
            "required": false,
            "schema": {
              "type": "string"
            }
          },
          "generated_cypher": {
            "nullable": true,
            "required": false,
            "schema": {
              "type": "string"
            }
          },
          "is_rejected": {
            "nullable": false,
            "required": true,
            "schema": {
              "type": "boolean"
            }
          },
          "is_valid": {
            "nullable": false,
            "required": true,
            "schema": {
              "type": "boolean"
            }
          },
          "latency_ms": {
            "nullable": false,
            "required": true,
            "schema": {
              "minimum": 0.0,
              "type": "integer"
            }
          },
          "question": {
            "nullable": false,
            "required": true,
            "schema": {
              "type": "string"
            }
          },
          "reject_reason": {
            "nullable": true,
            "required": false,
            "schema": {
              "type": "string"
            }
          },
          "row_count": {
            "nullable": false,
            "required": true,
            "schema": {
              "minimum": 0.0,
              "type": "integer"
            }
          },
          "rows": {
            "nullable": false,
            "required": true,
            "schema": {
              "items": {
                "additional_properties": true,
                "fields": {},
                "type": "object"
              },
              "type": "array"
            }
          }
        },
        "type": "object"
      },
      "shape": "object"
    },
    "422": {
      "schema": {
        "additional_properties": true,
        "fields": {
          "detail": {
            "nullable": false,
            "required": false,
            "schema": {
              "items": {
                "additional_properties": true,
                "fields": {
                  "loc": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "items": {
                        "type": "union",
                        "variants": [
                          {
                            "type": "string"
                          },
                          {
                            "type": "integer"
                          }
                        ]
                      },
                      "type": "array"
                    }
                  },
                  "msg": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "type": "string"
                    }
                  },
                  "type": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "type": "string"
                    }
                  }
                },
                "type": "object"
              },
              "type": "array"
            }
          }
        },
        "type": "object"
      },
      "shape": "object"
    }
  }
}
```

### 4.30 `POST /analytics/validate`

- 구분/담당: 팀필수 / D
- 요청: body: sql 1..20000
- 성공 응답: SqlValidateResponse
- 기타 상태: 422
- 정렬·제약: 12단계 검증
- 호환·경계: 실행 없음
- 계약 규칙:
  - 없음

```json
{
  "request": {
    "body": {
      "additional_properties": false,
      "fields": {
        "sql": {
          "nullable": false,
          "required": true,
          "schema": {
            "max_length": 20000,
            "min_length": 1,
            "type": "string"
          }
        }
      },
      "type": "object"
    },
    "header": {},
    "path": {},
    "query": {}
  },
  "responses": {
    "200": {
      "schema": {
        "additional_properties": false,
        "fields": {
          "checks": {
            "nullable": true,
            "required": false,
            "schema": {
              "items": {
                "additional_properties": false,
                "fields": {
                  "key": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "min_length": 1,
                      "type": "string"
                    }
                  },
                  "label": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "min_length": 1,
                      "type": "string"
                    }
                  },
                  "ok": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "type": "boolean"
                    }
                  }
                },
                "type": "object"
              },
              "type": "array"
            }
          },
          "normalized_sql": {
            "nullable": true,
            "required": false,
            "schema": {
              "type": "string"
            }
          },
          "reason": {
            "nullable": false,
            "required": true,
            "schema": {
              "type": "string"
            }
          },
          "valid": {
            "nullable": false,
            "required": true,
            "schema": {
              "type": "boolean"
            }
          }
        },
        "type": "object"
      },
      "shape": "object"
    },
    "422": {
      "schema": {
        "additional_properties": true,
        "fields": {
          "detail": {
            "nullable": false,
            "required": false,
            "schema": {
              "items": {
                "additional_properties": true,
                "fields": {
                  "loc": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "items": {
                        "type": "union",
                        "variants": [
                          {
                            "type": "string"
                          },
                          {
                            "type": "integer"
                          }
                        ]
                      },
                      "type": "array"
                    }
                  },
                  "msg": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "type": "string"
                    }
                  },
                  "type": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "type": "string"
                    }
                  }
                },
                "type": "object"
              },
              "type": "array"
            }
          }
        },
        "type": "object"
      },
      "shape": "object"
    }
  }
}
```

### 4.31 `GET /analytics/history`

- 구분/담당: 팀필수 / D
- 요청: query: page,size,filters
- 성공 응답: NlQueryHistoryResponse
- 기타 상태: 422
- 정렬·제약: asked_at DESC; nl_query_log_id DESC
- 호환·경계: 읽기 전용
- 계약 규칙:
  - 없음

```json
{
  "request": {
    "body": null,
    "header": {},
    "path": {},
    "query": {
      "date_from": {
        "nullable": true,
        "required": false,
        "schema": {
          "format": "date",
          "type": "string"
        }
      },
      "date_to": {
        "nullable": true,
        "required": false,
        "schema": {
          "format": "date",
          "type": "string"
        }
      },
      "is_rejected": {
        "nullable": true,
        "required": false,
        "schema": {
          "type": "boolean"
        }
      },
      "is_valid": {
        "nullable": true,
        "required": false,
        "schema": {
          "type": "boolean"
        }
      },
      "page": {
        "nullable": false,
        "required": false,
        "schema": {
          "default": 1,
          "minimum": 1,
          "type": "integer"
        }
      },
      "size": {
        "nullable": false,
        "required": false,
        "schema": {
          "default": 20,
          "maximum": 100,
          "minimum": 1,
          "type": "integer"
        }
      }
    }
  },
  "responses": {
    "200": {
      "schema": {
        "additional_properties": false,
        "fields": {
          "items": {
            "nullable": false,
            "required": true,
            "schema": {
              "items": {
                "additional_properties": false,
                "fields": {
                  "asked_at": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "format": "date-time",
                      "type": "string"
                    }
                  },
                  "error_msg": {
                    "nullable": true,
                    "required": false,
                    "schema": {
                      "type": "string"
                    }
                  },
                  "generated_sql": {
                    "nullable": true,
                    "required": false,
                    "schema": {
                      "type": "string"
                    }
                  },
                  "is_rejected": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "type": "boolean"
                    }
                  },
                  "is_valid": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "type": "boolean"
                    }
                  },
                  "latency_ms": {
                    "nullable": true,
                    "required": false,
                    "schema": {
                      "minimum": 0.0,
                      "type": "integer"
                    }
                  },
                  "nl_query_log_id": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "minimum": 1.0,
                      "type": "integer"
                    }
                  },
                  "outcome": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "enum": [
                        "DB_ERROR",
                        "POLICY_REJECTED",
                        "SUCCESS",
                        "VALIDATION_FAILED"
                      ],
                      "type": "string"
                    }
                  },
                  "question": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "type": "string"
                    }
                  },
                  "reject_reason": {
                    "nullable": true,
                    "required": false,
                    "schema": {
                      "type": "string"
                    }
                  },
                  "row_cnt": {
                    "nullable": true,
                    "required": false,
                    "schema": {
                      "minimum": 0.0,
                      "type": "integer"
                    }
                  }
                },
                "type": "object"
              },
              "type": "array"
            }
          },
          "page": {
            "nullable": false,
            "required": true,
            "schema": {
              "minimum": 1.0,
              "type": "integer"
            }
          },
          "size": {
            "nullable": false,
            "required": true,
            "schema": {
              "maximum": 100.0,
              "minimum": 1.0,
              "type": "integer"
            }
          },
          "total": {
            "nullable": false,
            "required": true,
            "schema": {
              "minimum": 0.0,
              "type": "integer"
            }
          }
        },
        "type": "object"
      },
      "shape": "object"
    },
    "422": {
      "schema": {
        "additional_properties": true,
        "fields": {
          "detail": {
            "nullable": false,
            "required": false,
            "schema": {
              "items": {
                "additional_properties": true,
                "fields": {
                  "loc": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "items": {
                        "type": "union",
                        "variants": [
                          {
                            "type": "string"
                          },
                          {
                            "type": "integer"
                          }
                        ]
                      },
                      "type": "array"
                    }
                  },
                  "msg": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "type": "string"
                    }
                  },
                  "type": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "type": "string"
                    }
                  }
                },
                "type": "object"
              },
              "type": "array"
            }
          }
        },
        "type": "object"
      },
      "shape": "object"
    }
  }
}
```

### 4.32 `GET /analytics/evaluations`

- 구분/담당: 팀필수 / D
- 요청: query: latest,page,size
- 성공 응답: EvaluationListResponse
- 기타 상태: 422
- 정렬·제약: executed_at DESC; run_id DESC
- 호환·경계: immutable 평가 artifact read-only projection
- 계약 규칙:
  - 없음

```json
{
  "request": {
    "body": null,
    "header": {},
    "path": {},
    "query": {
      "latest": {
        "nullable": false,
        "required": false,
        "schema": {
          "default": true,
          "type": "boolean"
        }
      },
      "page": {
        "nullable": false,
        "required": false,
        "schema": {
          "default": 1,
          "minimum": 1,
          "type": "integer"
        }
      },
      "size": {
        "nullable": false,
        "required": false,
        "schema": {
          "default": 20,
          "maximum": 100,
          "minimum": 1,
          "type": "integer"
        }
      }
    }
  },
  "responses": {
    "200": {
      "schema": {
        "additional_properties": false,
        "fields": {
          "items": {
            "nullable": false,
            "required": true,
            "schema": {
              "items": {
                "additional_properties": false,
                "fields": {
                  "accuracy": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "maximum": 1.0,
                      "minimum": 0.0,
                      "type": "number"
                    }
                  },
                  "correct": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "minimum": 0.0,
                      "type": "integer"
                    }
                  },
                  "defense_passed": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "minimum": 0.0,
                      "type": "integer"
                    }
                  },
                  "defense_total": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "minimum": 0.0,
                      "type": "integer"
                    }
                  },
                  "executed_at": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "format": "date-time",
                      "type": "string"
                    }
                  },
                  "items": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "items": {
                        "additional_properties": false,
                        "fields": {
                          "actual_result": {
                            "nullable": true,
                            "required": false,
                            "schema": {
                              "type": "any"
                            }
                          },
                          "actual_visualization": {
                            "nullable": true,
                            "required": false,
                            "schema": {
                              "additional_properties": false,
                              "fields": {
                                "chart_type": {
                                  "nullable": false,
                                  "required": true,
                                  "schema": {
                                    "enum": [
                                      "bar",
                                      "histogram",
                                      "line",
                                      "table"
                                    ],
                                    "type": "string"
                                  }
                                },
                                "x": {
                                  "nullable": true,
                                  "required": false,
                                  "schema": {
                                    "type": "string"
                                  }
                                },
                                "y": {
                                  "nullable": true,
                                  "required": false,
                                  "schema": {
                                    "type": "string"
                                  }
                                }
                              },
                              "type": "object"
                            }
                          },
                          "attempt_count": {
                            "nullable": false,
                            "required": true,
                            "schema": {
                              "minimum": 0.0,
                              "type": "integer"
                            }
                          },
                          "case_id": {
                            "nullable": false,
                            "required": true,
                            "schema": {
                              "min_length": 1,
                              "type": "string"
                            }
                          },
                          "case_type": {
                            "nullable": false,
                            "required": true,
                            "schema": {
                              "enum": [
                                "DEFENSE",
                                "GOLD"
                              ],
                              "type": "string"
                            }
                          },
                          "expected_result": {
                            "nullable": true,
                            "required": false,
                            "schema": {
                              "type": "any"
                            }
                          },
                          "expected_visualization": {
                            "nullable": true,
                            "required": false,
                            "schema": {
                              "additional_properties": false,
                              "fields": {
                                "chart_type": {
                                  "nullable": false,
                                  "required": true,
                                  "schema": {
                                    "enum": [
                                      "bar",
                                      "histogram",
                                      "line",
                                      "table"
                                    ],
                                    "type": "string"
                                  }
                                },
                                "x": {
                                  "nullable": true,
                                  "required": false,
                                  "schema": {
                                    "type": "string"
                                  }
                                },
                                "y": {
                                  "nullable": true,
                                  "required": false,
                                  "schema": {
                                    "type": "string"
                                  }
                                }
                              },
                              "type": "object"
                            }
                          },
                          "generated_sql": {
                            "nullable": true,
                            "required": false,
                            "schema": {
                              "type": "string"
                            }
                          },
                          "latency_ms": {
                            "nullable": true,
                            "required": false,
                            "schema": {
                              "minimum": 0.0,
                              "type": "integer"
                            }
                          },
                          "passed": {
                            "nullable": false,
                            "required": true,
                            "schema": {
                              "type": "boolean"
                            }
                          },
                          "question": {
                            "nullable": true,
                            "required": false,
                            "schema": {
                              "type": "string"
                            }
                          },
                          "reason": {
                            "nullable": true,
                            "required": false,
                            "schema": {
                              "type": "string"
                            }
                          }
                        },
                        "type": "object"
                      },
                      "type": "array"
                    }
                  },
                  "model": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "min_length": 1,
                      "type": "string"
                    }
                  },
                  "prompt_version": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "min_length": 1,
                      "type": "string"
                    }
                  },
                  "provider": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "min_length": 1,
                      "type": "string"
                    }
                  },
                  "run_id": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "min_length": 1,
                      "type": "string"
                    }
                  },
                  "temperature": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "type": "number"
                    }
                  },
                  "total": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "minimum": 0.0,
                      "type": "integer"
                    }
                  }
                },
                "type": "object"
              },
              "type": "array"
            }
          },
          "page": {
            "nullable": false,
            "required": true,
            "schema": {
              "minimum": 1.0,
              "type": "integer"
            }
          },
          "size": {
            "nullable": false,
            "required": true,
            "schema": {
              "maximum": 100.0,
              "minimum": 1.0,
              "type": "integer"
            }
          },
          "total": {
            "nullable": false,
            "required": true,
            "schema": {
              "minimum": 0.0,
              "type": "integer"
            }
          }
        },
        "type": "object"
      },
      "shape": "object"
    },
    "422": {
      "schema": {
        "additional_properties": true,
        "fields": {
          "detail": {
            "nullable": false,
            "required": false,
            "schema": {
              "items": {
                "additional_properties": true,
                "fields": {
                  "loc": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "items": {
                        "type": "union",
                        "variants": [
                          {
                            "type": "string"
                          },
                          {
                            "type": "integer"
                          }
                        ]
                      },
                      "type": "array"
                    }
                  },
                  "msg": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "type": "string"
                    }
                  },
                  "type": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "type": "string"
                    }
                  }
                },
                "type": "object"
              },
              "type": "array"
            }
          }
        },
        "type": "object"
      },
      "shape": "object"
    }
  }
}
```

### 4.33 `GET /audit-logs/paged`

- 구분/담당: 팀필수 / D
- 요청: query: page,size,filters
- 성공 응답: AuditLogPageResponse
- 기타 상태: 422,503
- 정렬·제약: occurred_at DESC; audit_id DESC
- 호환·경계: Agent 상세 감사와 구분; event count는 동일 전체 filter 기준
- 계약 규칙:
  - 없음

```json
{
  "request": {
    "body": null,
    "header": {},
    "path": {},
    "query": {
      "actor_type": {
        "nullable": true,
        "required": false,
        "schema": {
          "type": "string"
        }
      },
      "date_from": {
        "nullable": true,
        "required": false,
        "schema": {
          "format": "date",
          "type": "string"
        }
      },
      "date_to": {
        "nullable": true,
        "required": false,
        "schema": {
          "format": "date",
          "type": "string"
        }
      },
      "entity_id": {
        "nullable": true,
        "required": false,
        "schema": {
          "type": "string"
        }
      },
      "entity_type": {
        "nullable": true,
        "required": false,
        "schema": {
          "type": "string"
        }
      },
      "event_type": {
        "nullable": true,
        "required": false,
        "schema": {
          "type": "string"
        }
      },
      "page": {
        "nullable": false,
        "required": false,
        "schema": {
          "default": 1,
          "minimum": 1,
          "type": "integer"
        }
      },
      "size": {
        "nullable": false,
        "required": false,
        "schema": {
          "default": 20,
          "maximum": 100,
          "minimum": 1,
          "type": "integer"
        }
      }
    }
  },
  "responses": {
    "200": {
      "schema": {
        "additional_properties": true,
        "fields": {
          "event_type_counts": {
            "nullable": false,
            "required": true,
            "schema": {
              "additional_properties": {
                "type": "integer"
              },
              "fields": {},
              "type": "object"
            }
          },
          "event_types": {
            "nullable": false,
            "required": true,
            "schema": {
              "items": {
                "type": "string"
              },
              "type": "array"
            }
          },
          "items": {
            "nullable": false,
            "required": true,
            "schema": {
              "items": {
                "additional_properties": true,
                "fields": {
                  "actor": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "type": "string"
                    }
                  },
                  "actor_id": {
                    "nullable": true,
                    "required": true,
                    "schema": {
                      "type": "string"
                    }
                  },
                  "actor_type": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "type": "string"
                    }
                  },
                  "after": {
                    "nullable": true,
                    "required": true,
                    "schema": {
                      "additional_properties": true,
                      "fields": {},
                      "type": "object"
                    }
                  },
                  "at": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "format": "date-time",
                      "type": "string"
                    }
                  },
                  "audit_id": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "type": "integer"
                    }
                  },
                  "before": {
                    "nullable": true,
                    "required": true,
                    "schema": {
                      "additional_properties": true,
                      "fields": {},
                      "type": "object"
                    }
                  },
                  "detail": {
                    "nullable": true,
                    "required": true,
                    "schema": {
                      "type": "string"
                    }
                  },
                  "entity": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "type": "string"
                    }
                  },
                  "entity_id": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "type": "string"
                    }
                  },
                  "entity_type": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "type": "string"
                    }
                  },
                  "event": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "type": "string"
                    }
                  },
                  "event_type": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "type": "string"
                    }
                  },
                  "occurred_at": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "format": "date-time",
                      "type": "string"
                    }
                  }
                },
                "type": "object"
              },
              "type": "array"
            }
          },
          "page": {
            "nullable": false,
            "required": true,
            "schema": {
              "type": "integer"
            }
          },
          "size": {
            "nullable": false,
            "required": true,
            "schema": {
              "type": "integer"
            }
          },
          "total": {
            "nullable": false,
            "required": true,
            "schema": {
              "type": "integer"
            }
          }
        },
        "type": "object"
      },
      "shape": "object"
    },
    "422": {
      "schema": {
        "additional_properties": true,
        "fields": {
          "detail": {
            "nullable": false,
            "required": false,
            "schema": {
              "items": {
                "additional_properties": true,
                "fields": {
                  "loc": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "items": {
                        "type": "union",
                        "variants": [
                          {
                            "type": "string"
                          },
                          {
                            "type": "integer"
                          }
                        ]
                      },
                      "type": "array"
                    }
                  },
                  "msg": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "type": "string"
                    }
                  },
                  "type": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "type": "string"
                    }
                  }
                },
                "type": "object"
              },
              "type": "array"
            }
          }
        },
        "type": "object"
      },
      "shape": "object"
    }
  }
}
```

### 4.34 `GET /health`

- 구분/담당: 운영 / Common
- 요청: 없음
- 성공 응답: HealthResponse
- 기타 상태: 500
- 정렬·제약: 업무 API 수 제외
- 호환·경계: 외부 의존성 미검사
- 계약 규칙:
  - 없음

```json
{
  "request": {
    "body": null,
    "header": {},
    "path": {},
    "query": {}
  },
  "responses": {
    "200": {
      "schema": {
        "additional_properties": false,
        "fields": {
          "status": {
            "nullable": false,
            "required": true,
            "schema": {
              "type": "string"
            }
          }
        },
        "type": "object"
      },
      "shape": "object"
    }
  }
}
```

### 4.35 `GET /health/ready`

- 구분/담당: 운영 / Common
- 요청: 없음
- 성공 응답: ReadinessResponse
- 기타 상태: 503
- 정렬·제약: 업무 API 수 제외
- 호환·경계: PostgreSQL Runtime epoch·schema·권한; reference migration marker; Neo4j 44/85 marker·fingerprint; RAG 3 IDs·source/corrected hash·chunk·NULL embedding 0·dimension 1024·search smoke; n8n; Kafka topics; 실패도 같은 DTO+NOT_READY
- 계약 규칙:
  - all checks PASS yields status READY and HTTP 200
  - any check FAIL yields status NOT_READY and HTTP 503

```json
{
  "request": {
    "body": null,
    "header": {},
    "path": {},
    "query": {}
  },
  "responses": {
    "200": {
      "schema": {
        "additional_properties": false,
        "fields": {
          "checks": {
            "nullable": false,
            "required": true,
            "schema": {
              "additional_properties": false,
              "fields": {
                "kafka": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "additional_properties": false,
                    "fields": {
                      "latency_ms": {
                        "nullable": false,
                        "required": true,
                        "schema": {
                          "minimum": 0.0,
                          "type": "integer"
                        }
                      },
                      "reason_code": {
                        "nullable": true,
                        "required": true,
                        "schema": {
                          "enum": [
                            "CONTRACT_MISMATCH",
                            "DEPENDENCY_UNAVAILABLE",
                            "KAFKA_LAG_STALE",
                            "NOT_CONFIGURED",
                            "RAG_MODEL_NOT_READY",
                            "TIMEOUT"
                          ],
                          "type": "string"
                        }
                      },
                      "status": {
                        "nullable": false,
                        "required": true,
                        "schema": {
                          "enum": [
                            "FAIL",
                            "PASS"
                          ],
                          "type": "string"
                        }
                      }
                    },
                    "type": "object"
                  }
                },
                "n8n": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "additional_properties": false,
                    "fields": {
                      "latency_ms": {
                        "nullable": false,
                        "required": true,
                        "schema": {
                          "minimum": 0.0,
                          "type": "integer"
                        }
                      },
                      "reason_code": {
                        "nullable": true,
                        "required": true,
                        "schema": {
                          "enum": [
                            "CONTRACT_MISMATCH",
                            "DEPENDENCY_UNAVAILABLE",
                            "KAFKA_LAG_STALE",
                            "NOT_CONFIGURED",
                            "RAG_MODEL_NOT_READY",
                            "TIMEOUT"
                          ],
                          "type": "string"
                        }
                      },
                      "status": {
                        "nullable": false,
                        "required": true,
                        "schema": {
                          "enum": [
                            "FAIL",
                            "PASS"
                          ],
                          "type": "string"
                        }
                      }
                    },
                    "type": "object"
                  }
                },
                "neo4j": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "additional_properties": false,
                    "fields": {
                      "latency_ms": {
                        "nullable": false,
                        "required": true,
                        "schema": {
                          "minimum": 0.0,
                          "type": "integer"
                        }
                      },
                      "reason_code": {
                        "nullable": true,
                        "required": true,
                        "schema": {
                          "enum": [
                            "CONTRACT_MISMATCH",
                            "DEPENDENCY_UNAVAILABLE",
                            "KAFKA_LAG_STALE",
                            "NOT_CONFIGURED",
                            "RAG_MODEL_NOT_READY",
                            "TIMEOUT"
                          ],
                          "type": "string"
                        }
                      },
                      "status": {
                        "nullable": false,
                        "required": true,
                        "schema": {
                          "enum": [
                            "FAIL",
                            "PASS"
                          ],
                          "type": "string"
                        }
                      }
                    },
                    "type": "object"
                  }
                },
                "postgresql_runtime": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "additional_properties": false,
                    "fields": {
                      "latency_ms": {
                        "nullable": false,
                        "required": true,
                        "schema": {
                          "minimum": 0.0,
                          "type": "integer"
                        }
                      },
                      "reason_code": {
                        "nullable": true,
                        "required": true,
                        "schema": {
                          "enum": [
                            "CONTRACT_MISMATCH",
                            "DEPENDENCY_UNAVAILABLE",
                            "KAFKA_LAG_STALE",
                            "NOT_CONFIGURED",
                            "RAG_MODEL_NOT_READY",
                            "TIMEOUT"
                          ],
                          "type": "string"
                        }
                      },
                      "status": {
                        "nullable": false,
                        "required": true,
                        "schema": {
                          "enum": [
                            "FAIL",
                            "PASS"
                          ],
                          "type": "string"
                        }
                      }
                    },
                    "type": "object"
                  }
                },
                "rag": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "additional_properties": false,
                    "fields": {
                      "latency_ms": {
                        "nullable": false,
                        "required": true,
                        "schema": {
                          "minimum": 0.0,
                          "type": "integer"
                        }
                      },
                      "reason_code": {
                        "nullable": true,
                        "required": true,
                        "schema": {
                          "enum": [
                            "CONTRACT_MISMATCH",
                            "DEPENDENCY_UNAVAILABLE",
                            "KAFKA_LAG_STALE",
                            "NOT_CONFIGURED",
                            "RAG_MODEL_NOT_READY",
                            "TIMEOUT"
                          ],
                          "type": "string"
                        }
                      },
                      "status": {
                        "nullable": false,
                        "required": true,
                        "schema": {
                          "enum": [
                            "FAIL",
                            "PASS"
                          ],
                          "type": "string"
                        }
                      }
                    },
                    "type": "object"
                  }
                },
                "reference_migration": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "additional_properties": false,
                    "fields": {
                      "latency_ms": {
                        "nullable": false,
                        "required": true,
                        "schema": {
                          "minimum": 0.0,
                          "type": "integer"
                        }
                      },
                      "reason_code": {
                        "nullable": true,
                        "required": true,
                        "schema": {
                          "enum": [
                            "CONTRACT_MISMATCH",
                            "DEPENDENCY_UNAVAILABLE",
                            "KAFKA_LAG_STALE",
                            "NOT_CONFIGURED",
                            "RAG_MODEL_NOT_READY",
                            "TIMEOUT"
                          ],
                          "type": "string"
                        }
                      },
                      "status": {
                        "nullable": false,
                        "required": true,
                        "schema": {
                          "enum": [
                            "FAIL",
                            "PASS"
                          ],
                          "type": "string"
                        }
                      }
                    },
                    "type": "object"
                  }
                }
              },
              "type": "object"
            }
          },
          "dataset_epoch": {
            "nullable": false,
            "required": true,
            "schema": {
              "type": "string"
            }
          },
          "status": {
            "nullable": false,
            "required": true,
            "schema": {
              "enum": [
                "NOT_READY",
                "READY"
              ],
              "type": "string"
            }
          }
        },
        "type": "object"
      },
      "shape": "object"
    },
    "503": {
      "schema": {
        "additional_properties": false,
        "fields": {
          "checks": {
            "nullable": false,
            "required": true,
            "schema": {
              "additional_properties": false,
              "fields": {
                "kafka": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "additional_properties": false,
                    "fields": {
                      "latency_ms": {
                        "nullable": false,
                        "required": true,
                        "schema": {
                          "minimum": 0.0,
                          "type": "integer"
                        }
                      },
                      "reason_code": {
                        "nullable": true,
                        "required": true,
                        "schema": {
                          "enum": [
                            "CONTRACT_MISMATCH",
                            "DEPENDENCY_UNAVAILABLE",
                            "KAFKA_LAG_STALE",
                            "NOT_CONFIGURED",
                            "RAG_MODEL_NOT_READY",
                            "TIMEOUT"
                          ],
                          "type": "string"
                        }
                      },
                      "status": {
                        "nullable": false,
                        "required": true,
                        "schema": {
                          "enum": [
                            "FAIL",
                            "PASS"
                          ],
                          "type": "string"
                        }
                      }
                    },
                    "type": "object"
                  }
                },
                "n8n": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "additional_properties": false,
                    "fields": {
                      "latency_ms": {
                        "nullable": false,
                        "required": true,
                        "schema": {
                          "minimum": 0.0,
                          "type": "integer"
                        }
                      },
                      "reason_code": {
                        "nullable": true,
                        "required": true,
                        "schema": {
                          "enum": [
                            "CONTRACT_MISMATCH",
                            "DEPENDENCY_UNAVAILABLE",
                            "KAFKA_LAG_STALE",
                            "NOT_CONFIGURED",
                            "RAG_MODEL_NOT_READY",
                            "TIMEOUT"
                          ],
                          "type": "string"
                        }
                      },
                      "status": {
                        "nullable": false,
                        "required": true,
                        "schema": {
                          "enum": [
                            "FAIL",
                            "PASS"
                          ],
                          "type": "string"
                        }
                      }
                    },
                    "type": "object"
                  }
                },
                "neo4j": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "additional_properties": false,
                    "fields": {
                      "latency_ms": {
                        "nullable": false,
                        "required": true,
                        "schema": {
                          "minimum": 0.0,
                          "type": "integer"
                        }
                      },
                      "reason_code": {
                        "nullable": true,
                        "required": true,
                        "schema": {
                          "enum": [
                            "CONTRACT_MISMATCH",
                            "DEPENDENCY_UNAVAILABLE",
                            "KAFKA_LAG_STALE",
                            "NOT_CONFIGURED",
                            "RAG_MODEL_NOT_READY",
                            "TIMEOUT"
                          ],
                          "type": "string"
                        }
                      },
                      "status": {
                        "nullable": false,
                        "required": true,
                        "schema": {
                          "enum": [
                            "FAIL",
                            "PASS"
                          ],
                          "type": "string"
                        }
                      }
                    },
                    "type": "object"
                  }
                },
                "postgresql_runtime": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "additional_properties": false,
                    "fields": {
                      "latency_ms": {
                        "nullable": false,
                        "required": true,
                        "schema": {
                          "minimum": 0.0,
                          "type": "integer"
                        }
                      },
                      "reason_code": {
                        "nullable": true,
                        "required": true,
                        "schema": {
                          "enum": [
                            "CONTRACT_MISMATCH",
                            "DEPENDENCY_UNAVAILABLE",
                            "KAFKA_LAG_STALE",
                            "NOT_CONFIGURED",
                            "RAG_MODEL_NOT_READY",
                            "TIMEOUT"
                          ],
                          "type": "string"
                        }
                      },
                      "status": {
                        "nullable": false,
                        "required": true,
                        "schema": {
                          "enum": [
                            "FAIL",
                            "PASS"
                          ],
                          "type": "string"
                        }
                      }
                    },
                    "type": "object"
                  }
                },
                "rag": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "additional_properties": false,
                    "fields": {
                      "latency_ms": {
                        "nullable": false,
                        "required": true,
                        "schema": {
                          "minimum": 0.0,
                          "type": "integer"
                        }
                      },
                      "reason_code": {
                        "nullable": true,
                        "required": true,
                        "schema": {
                          "enum": [
                            "CONTRACT_MISMATCH",
                            "DEPENDENCY_UNAVAILABLE",
                            "KAFKA_LAG_STALE",
                            "NOT_CONFIGURED",
                            "RAG_MODEL_NOT_READY",
                            "TIMEOUT"
                          ],
                          "type": "string"
                        }
                      },
                      "status": {
                        "nullable": false,
                        "required": true,
                        "schema": {
                          "enum": [
                            "FAIL",
                            "PASS"
                          ],
                          "type": "string"
                        }
                      }
                    },
                    "type": "object"
                  }
                },
                "reference_migration": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "additional_properties": false,
                    "fields": {
                      "latency_ms": {
                        "nullable": false,
                        "required": true,
                        "schema": {
                          "minimum": 0.0,
                          "type": "integer"
                        }
                      },
                      "reason_code": {
                        "nullable": true,
                        "required": true,
                        "schema": {
                          "enum": [
                            "CONTRACT_MISMATCH",
                            "DEPENDENCY_UNAVAILABLE",
                            "KAFKA_LAG_STALE",
                            "NOT_CONFIGURED",
                            "RAG_MODEL_NOT_READY",
                            "TIMEOUT"
                          ],
                          "type": "string"
                        }
                      },
                      "status": {
                        "nullable": false,
                        "required": true,
                        "schema": {
                          "enum": [
                            "FAIL",
                            "PASS"
                          ],
                          "type": "string"
                        }
                      }
                    },
                    "type": "object"
                  }
                }
              },
              "type": "object"
            }
          },
          "dataset_epoch": {
            "nullable": false,
            "required": true,
            "schema": {
              "type": "string"
            }
          },
          "status": {
            "nullable": false,
            "required": true,
            "schema": {
              "enum": [
                "NOT_READY",
                "READY"
              ],
              "type": "string"
            }
          }
        },
        "type": "object"
      },
      "shape": "object"
    }
  }
}
```

## 5. DTO 상세

### 5.1 `ActionCode`

```json
{
  "enum": [
    "EQP_HOLD",
    "MONITORING",
    "WARNING"
  ],
  "type": "string"
}
```

### 5.2 `ActionDeliveryDetailItem`

```json
{
  "additional_properties": false,
  "fields": {
    "channel": {
      "nullable": false,
      "required": true,
      "schema": {
        "enum": [
          "EMAIL",
          "MES"
        ],
        "type": "string"
      }
    },
    "completed_at": {
      "nullable": true,
      "required": true,
      "schema": {
        "format": "date-time",
        "type": "string"
      }
    },
    "started_at": {
      "nullable": true,
      "required": true,
      "schema": {
        "format": "date-time",
        "type": "string"
      }
    },
    "status": {
      "nullable": false,
      "required": true,
      "schema": {
        "enum": [
          "BLOCKED",
          "CANCELED",
          "FAILED",
          "SENDING",
          "SENT",
          "UNKNOWN",
          "WAITING"
        ],
        "type": "string"
      }
    }
  },
  "type": "object"
}
```

### 5.3 `ActionDeliveryItem`

```json
{
  "additional_properties": false,
  "fields": {
    "channel": {
      "nullable": false,
      "required": true,
      "schema": {
        "enum": [
          "EMAIL",
          "MES"
        ],
        "type": "string"
      }
    },
    "status": {
      "nullable": false,
      "required": true,
      "schema": {
        "enum": [
          "BLOCKED",
          "CANCELED",
          "FAILED",
          "SENDING",
          "SENT",
          "UNKNOWN",
          "WAITING"
        ],
        "type": "string"
      }
    }
  },
  "type": "object"
}
```

### 5.4 `ActionDetailResponse`

```json
{
  "additional_properties": false,
  "fields": {
    "action_code": {
      "nullable": false,
      "required": true,
      "schema": {
        "enum": [
          "EQP_HOLD",
          "MONITORING",
          "WARNING"
        ],
        "type": "string"
      }
    },
    "action_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "agent_run_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "approval_status": {
      "nullable": true,
      "required": true,
      "schema": {
        "enum": [
          "APPROVED",
          "PENDING",
          "REJECTED"
        ],
        "type": "string"
      }
    },
    "chamber": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "chamber_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "created_at": {
      "nullable": false,
      "required": true,
      "schema": {
        "format": "date-time",
        "type": "string"
      }
    },
    "created_by_agent_run_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "deliveries": {
      "nullable": false,
      "required": true,
      "schema": {
        "items": {
          "additional_properties": false,
          "fields": {
            "channel": {
              "nullable": false,
              "required": true,
              "schema": {
                "enum": [
                  "EMAIL",
                  "MES"
                ],
                "type": "string"
              }
            },
            "completed_at": {
              "nullable": true,
              "required": true,
              "schema": {
                "format": "date-time",
                "type": "string"
              }
            },
            "started_at": {
              "nullable": true,
              "required": true,
              "schema": {
                "format": "date-time",
                "type": "string"
              }
            },
            "status": {
              "nullable": false,
              "required": true,
              "schema": {
                "enum": [
                  "BLOCKED",
                  "CANCELED",
                  "FAILED",
                  "SENDING",
                  "SENT",
                  "UNKNOWN",
                  "WAITING"
                ],
                "type": "string"
              }
            }
          },
          "type": "object"
        },
        "type": "array"
      }
    },
    "equipment": {
      "nullable": true,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "equipment_id": {
      "nullable": true,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "lot": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "lot_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "reason": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    }
  },
  "type": "object"
}
```

### 5.5 `ActionItem`

```json
{
  "additional_properties": false,
  "fields": {
    "action_code": {
      "nullable": false,
      "required": true,
      "schema": {
        "enum": [
          "EQP_HOLD",
          "MONITORING",
          "WARNING"
        ],
        "type": "string"
      }
    },
    "action_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "agent_run_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "approval_status": {
      "nullable": true,
      "required": true,
      "schema": {
        "enum": [
          "APPROVED",
          "PENDING",
          "REJECTED"
        ],
        "type": "string"
      }
    },
    "chamber": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "chamber_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "created_at": {
      "nullable": false,
      "required": true,
      "schema": {
        "format": "date-time",
        "type": "string"
      }
    },
    "created_by_agent_run_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "deliveries": {
      "nullable": false,
      "required": true,
      "schema": {
        "items": {
          "additional_properties": false,
          "fields": {
            "channel": {
              "nullable": false,
              "required": true,
              "schema": {
                "enum": [
                  "EMAIL",
                  "MES"
                ],
                "type": "string"
              }
            },
            "status": {
              "nullable": false,
              "required": true,
              "schema": {
                "enum": [
                  "BLOCKED",
                  "CANCELED",
                  "FAILED",
                  "SENDING",
                  "SENT",
                  "UNKNOWN",
                  "WAITING"
                ],
                "type": "string"
              }
            }
          },
          "type": "object"
        },
        "type": "array"
      }
    },
    "equipment": {
      "nullable": true,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "equipment_id": {
      "nullable": true,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "lot": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "lot_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "reason": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    }
  },
  "type": "object"
}
```

### 5.6 `AgentAskCompatibilityEvidence`

```json
{
  "additional_properties": false,
  "fields": {
    "chunk_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "doc_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "document_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "section": {
      "nullable": true,
      "required": true,
      "schema": {
        "type": "string"
      }
    }
  },
  "type": "object"
}
```

### 5.7 `AgentAskRequest`

```json
{
  "additional_properties": false,
  "fields": {
    "question": {
      "nullable": false,
      "required": true,
      "schema": {
        "max_length": 1000,
        "min_length": 1,
        "type": "string"
      }
    }
  },
  "type": "object"
}
```

### 5.8 `AgentAskResponse`

```json
{
  "additional_properties": false,
  "fields": {
    "answer": {
      "nullable": false,
      "required": true,
      "schema": {
        "type": "string"
      }
    },
    "confidence": {
      "nullable": true,
      "required": true,
      "schema": {
        "maximum": 1,
        "minimum": 0,
        "type": "number"
      }
    },
    "evidence": {
      "nullable": true,
      "required": true,
      "schema": {
        "additional_properties": false,
        "fields": {
          "chunk_id": {
            "nullable": false,
            "required": true,
            "schema": {
              "min_length": 1,
              "type": "string"
            }
          },
          "doc_id": {
            "nullable": false,
            "required": true,
            "schema": {
              "min_length": 1,
              "type": "string"
            }
          },
          "document_id": {
            "nullable": false,
            "required": true,
            "schema": {
              "min_length": 1,
              "type": "string"
            }
          },
          "section": {
            "nullable": true,
            "required": true,
            "schema": {
              "type": "string"
            }
          }
        },
        "type": "object"
      }
    },
    "evidence_items": {
      "nullable": false,
      "required": true,
      "schema": {
        "items": {
          "discriminator": "type",
          "type": "discriminated_union",
          "variants": {
            "ALARM": {
              "additional_properties": false,
              "fields": {
                "excerpt": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "type": "string"
                  }
                },
                "source_id": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "min_length": 1,
                    "type": "string"
                  }
                },
                "title": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "min_length": 1,
                    "type": "string"
                  }
                },
                "type": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "enum": [
                      "ALARM"
                    ],
                    "type": "string"
                  }
                }
              },
              "type": "object"
            },
            "DOCUMENT": {
              "additional_properties": false,
              "fields": {
                "chunk_id": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "min_length": 1,
                    "type": "string"
                  }
                },
                "document_id": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "min_length": 1,
                    "type": "string"
                  }
                },
                "excerpt": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "type": "string"
                  }
                },
                "section": {
                  "nullable": true,
                  "required": true,
                  "schema": {
                    "type": "string"
                  }
                },
                "source_id": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "min_length": 1,
                    "type": "string"
                  }
                },
                "title": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "min_length": 1,
                    "type": "string"
                  }
                },
                "type": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "enum": [
                      "DOCUMENT"
                    ],
                    "type": "string"
                  }
                }
              },
              "type": "object"
            },
            "GRAPH": {
              "additional_properties": false,
              "fields": {
                "excerpt": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "type": "string"
                  }
                },
                "graph_revision": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "pattern": "^[0-9a-f]{64}$",
                    "type": "string"
                  }
                },
                "relation_id": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "pattern": "^REL-[0-9a-f]{20}$",
                    "type": "string"
                  }
                },
                "source_id": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "min_length": 1,
                    "type": "string"
                  }
                },
                "title": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "min_length": 1,
                    "type": "string"
                  }
                },
                "type": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "enum": [
                      "GRAPH"
                    ],
                    "type": "string"
                  }
                }
              },
              "type": "object"
            },
            "METROLOGY": {
              "additional_properties": false,
              "fields": {
                "excerpt": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "type": "string"
                  }
                },
                "source_id": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "min_length": 1,
                    "type": "string"
                  }
                },
                "title": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "min_length": 1,
                    "type": "string"
                  }
                },
                "type": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "enum": [
                      "METROLOGY"
                    ],
                    "type": "string"
                  }
                }
              },
              "rules": [
                "metrology.alarm_result is forbidden"
              ],
              "type": "object"
            },
            "TRACE": {
              "additional_properties": false,
              "fields": {
                "excerpt": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "type": "string"
                  }
                },
                "source_id": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "min_length": 1,
                    "type": "string"
                  }
                },
                "title": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "min_length": 1,
                    "type": "string"
                  }
                },
                "type": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "enum": [
                      "TRACE"
                    ],
                    "type": "string"
                  }
                }
              },
              "type": "object"
            }
          }
        },
        "type": "array"
      }
    },
    "limit": {
      "nullable": false,
      "required": true,
      "schema": {
        "type": "string"
      }
    },
    "limitations": {
      "nullable": false,
      "required": true,
      "schema": {
        "items": {
          "type": "string"
        },
        "type": "array"
      }
    },
    "predicted_fault_code": {
      "nullable": true,
      "required": true,
      "schema": {
        "enum": [
          "FOC",
          "MFD",
          "OTH",
          "RFM",
          "TMD"
        ],
        "type": "string"
      }
    },
    "recommended_action": {
      "nullable": true,
      "required": true,
      "schema": {
        "enum": [
          "EQP_HOLD",
          "MONITORING",
          "WARNING"
        ],
        "type": "string"
      }
    },
    "title": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "tools": {
      "nullable": false,
      "required": true,
      "schema": {
        "items": {
          "additional_properties": false,
          "fields": {
            "name": {
              "nullable": false,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            },
            "result": {
              "nullable": false,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            },
            "result_summary": {
              "nullable": false,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            },
            "status": {
              "nullable": false,
              "required": true,
              "schema": {
                "enum": [
                  "ERROR",
                  "SUCCESS",
                  "TIMEOUT"
                ],
                "type": "string"
              }
            },
            "tool_name": {
              "nullable": false,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            }
          },
          "type": "object"
        },
        "type": "array"
      }
    }
  },
  "rules": [
    "fault_code and ground_truth_fault_code are forbidden"
  ],
  "type": "object"
}
```

### 5.9 `AgentRunAccepted`

```json
{
  "additional_properties": false,
  "fields": {
    "agent_run_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "alarm": {
      "nullable": false,
      "required": true,
      "schema": {
        "additional_properties": false,
        "fields": {
          "alarm_id": {
            "nullable": false,
            "required": true,
            "schema": {
              "min_length": 1,
              "type": "string"
            }
          },
          "source": {
            "nullable": false,
            "required": true,
            "schema": {
              "enum": [
                "R03",
                "SUMMARY",
                "TRACE"
              ],
              "type": "string"
            }
          }
        },
        "type": "object"
      }
    },
    "status": {
      "nullable": false,
      "required": true,
      "schema": {
        "enum": [
          "RUNNING"
        ],
        "type": "string"
      }
    }
  },
  "type": "object"
}
```

### 5.10 `AgentRunAcceptedResponse`

```json
{
  "additional_properties": false,
  "fields": {
    "agent_run_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "alarm": {
      "nullable": false,
      "required": true,
      "schema": {
        "additional_properties": false,
        "fields": {
          "alarm_id": {
            "nullable": false,
            "required": true,
            "schema": {
              "min_length": 1,
              "type": "string"
            }
          },
          "source": {
            "nullable": false,
            "required": true,
            "schema": {
              "enum": [
                "R03",
                "SUMMARY",
                "TRACE"
              ],
              "type": "string"
            }
          }
        },
        "type": "object"
      }
    },
    "status": {
      "nullable": false,
      "required": true,
      "schema": {
        "type": "string"
      }
    }
  },
  "type": "object"
}
```

### 5.11 `AgentRunActionItem`

```json
{
  "additional_properties": false,
  "fields": {
    "action_code": {
      "nullable": false,
      "required": true,
      "schema": {
        "enum": [
          "EQP_HOLD",
          "MONITORING",
          "WARNING"
        ],
        "type": "string"
      }
    },
    "action_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "agent_run_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "approval_status": {
      "nullable": true,
      "required": true,
      "schema": {
        "enum": [
          "APPROVED",
          "PENDING",
          "REJECTED"
        ],
        "type": "string"
      }
    },
    "deliveries": {
      "nullable": false,
      "required": true,
      "schema": {
        "items": {
          "additional_properties": false,
          "fields": {
            "channel": {
              "nullable": false,
              "required": true,
              "schema": {
                "enum": [
                  "EMAIL",
                  "MES"
                ],
                "type": "string"
              }
            },
            "status": {
              "nullable": false,
              "required": true,
              "schema": {
                "enum": [
                  "BLOCKED",
                  "CANCELED",
                  "FAILED",
                  "SENDING",
                  "SENT",
                  "UNKNOWN",
                  "WAITING"
                ],
                "type": "string"
              }
            }
          },
          "type": "object"
        },
        "type": "array"
      }
    },
    "reason": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    }
  },
  "type": "object"
}
```

### 5.12 `AgentRunApprovalItem`

```json
{
  "additional_properties": false,
  "fields": {
    "action_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "agent_run_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "approval_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "decided_at": {
      "nullable": true,
      "required": true,
      "schema": {
        "format": "date-time",
        "type": "string"
      }
    },
    "decided_by": {
      "nullable": true,
      "required": true,
      "schema": {
        "type": "string"
      }
    },
    "decision_comment": {
      "nullable": true,
      "required": true,
      "schema": {
        "type": "string"
      }
    },
    "status": {
      "nullable": false,
      "required": true,
      "schema": {
        "enum": [
          "APPROVED",
          "PENDING",
          "REJECTED"
        ],
        "type": "string"
      }
    }
  },
  "type": "object"
}
```

### 5.13 `AgentRunCreateRequest`

```json
{
  "additional_properties": false,
  "fields": {
    "alarm": {
      "nullable": false,
      "required": true,
      "schema": {
        "additional_properties": false,
        "fields": {
          "alarm_id": {
            "nullable": false,
            "required": true,
            "schema": {
              "min_length": 1,
              "type": "string"
            }
          },
          "source": {
            "nullable": false,
            "required": true,
            "schema": {
              "enum": [
                "R03",
                "SUMMARY",
                "TRACE"
              ],
              "type": "string"
            }
          }
        },
        "type": "object"
      }
    }
  },
  "type": "object"
}
```

### 5.14 `AgentRunDetailResponse`

```json
{
  "additional_properties": false,
  "fields": {
    "action": {
      "nullable": true,
      "required": true,
      "schema": {
        "additional_properties": false,
        "fields": {
          "action_code": {
            "nullable": false,
            "required": true,
            "schema": {
              "enum": [
                "EQP_HOLD",
                "MONITORING",
                "WARNING"
              ],
              "type": "string"
            }
          },
          "action_id": {
            "nullable": false,
            "required": true,
            "schema": {
              "min_length": 1,
              "type": "string"
            }
          },
          "agent_run_id": {
            "nullable": false,
            "required": true,
            "schema": {
              "min_length": 1,
              "type": "string"
            }
          },
          "approval_status": {
            "nullable": true,
            "required": true,
            "schema": {
              "enum": [
                "APPROVED",
                "PENDING",
                "REJECTED"
              ],
              "type": "string"
            }
          },
          "deliveries": {
            "nullable": false,
            "required": true,
            "schema": {
              "items": {
                "additional_properties": false,
                "fields": {
                  "channel": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "enum": [
                        "EMAIL",
                        "MES"
                      ],
                      "type": "string"
                    }
                  },
                  "status": {
                    "nullable": false,
                    "required": true,
                    "schema": {
                      "enum": [
                        "BLOCKED",
                        "CANCELED",
                        "FAILED",
                        "SENDING",
                        "SENT",
                        "UNKNOWN",
                        "WAITING"
                      ],
                      "type": "string"
                    }
                  }
                },
                "type": "object"
              },
              "type": "array"
            }
          },
          "reason": {
            "nullable": false,
            "required": true,
            "schema": {
              "min_length": 1,
              "type": "string"
            }
          }
        },
        "type": "object"
      }
    },
    "action_id": {
      "nullable": true,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "agent_run_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "alarm_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "alarm_source": {
      "nullable": false,
      "required": true,
      "schema": {
        "enum": [
          "R03",
          "SUMMARY",
          "TRACE"
        ],
        "type": "string"
      }
    },
    "approval": {
      "nullable": true,
      "required": true,
      "schema": {
        "additional_properties": false,
        "fields": {
          "action_id": {
            "nullable": false,
            "required": true,
            "schema": {
              "min_length": 1,
              "type": "string"
            }
          },
          "agent_run_id": {
            "nullable": false,
            "required": true,
            "schema": {
              "min_length": 1,
              "type": "string"
            }
          },
          "approval_id": {
            "nullable": false,
            "required": true,
            "schema": {
              "min_length": 1,
              "type": "string"
            }
          },
          "decided_at": {
            "nullable": true,
            "required": true,
            "schema": {
              "format": "date-time",
              "type": "string"
            }
          },
          "decided_by": {
            "nullable": true,
            "required": true,
            "schema": {
              "type": "string"
            }
          },
          "decision_comment": {
            "nullable": true,
            "required": true,
            "schema": {
              "type": "string"
            }
          },
          "status": {
            "nullable": false,
            "required": true,
            "schema": {
              "enum": [
                "APPROVED",
                "PENDING",
                "REJECTED"
              ],
              "type": "string"
            }
          }
        },
        "type": "object"
      }
    },
    "approval_id": {
      "nullable": true,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "chamber": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "chamber_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "confidence": {
      "nullable": true,
      "required": true,
      "schema": {
        "maximum": 1.0,
        "minimum": 0.0,
        "type": "number"
      }
    },
    "created_at": {
      "nullable": false,
      "required": true,
      "schema": {
        "format": "date-time",
        "type": "string"
      }
    },
    "deliveries": {
      "nullable": false,
      "required": true,
      "schema": {
        "items": {
          "additional_properties": false,
          "fields": {
            "channel": {
              "nullable": false,
              "required": true,
              "schema": {
                "enum": [
                  "EMAIL",
                  "MES"
                ],
                "type": "string"
              }
            },
            "status": {
              "nullable": false,
              "required": true,
              "schema": {
                "enum": [
                  "BLOCKED",
                  "CANCELED",
                  "FAILED",
                  "SENDING",
                  "SENT",
                  "UNKNOWN",
                  "WAITING"
                ],
                "type": "string"
              }
            }
          },
          "type": "object"
        },
        "type": "array"
      }
    },
    "evidence_items": {
      "nullable": false,
      "required": true,
      "schema": {
        "items": {
          "discriminator": "type",
          "type": "discriminated_union",
          "variants": {
            "ALARM": {
              "additional_properties": false,
              "fields": {
                "alarm": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "additional_properties": false,
                    "fields": {
                      "alarm_id": {
                        "nullable": false,
                        "required": true,
                        "schema": {
                          "min_length": 1,
                          "type": "string"
                        }
                      },
                      "source": {
                        "nullable": false,
                        "required": true,
                        "schema": {
                          "enum": [
                            "R03",
                            "SUMMARY",
                            "TRACE"
                          ],
                          "type": "string"
                        }
                      }
                    },
                    "type": "object"
                  }
                },
                "excerpt": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "min_length": 1,
                    "type": "string"
                  }
                },
                "source_id": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "min_length": 1,
                    "type": "string"
                  }
                },
                "title": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "min_length": 1,
                    "type": "string"
                  }
                },
                "type": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "type": "string"
                  }
                }
              },
              "type": "object"
            },
            "DOCUMENT": {
              "additional_properties": false,
              "fields": {
                "chunk_id": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "min_length": 1,
                    "type": "string"
                  }
                },
                "document_id": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "min_length": 1,
                    "type": "string"
                  }
                },
                "excerpt": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "min_length": 1,
                    "type": "string"
                  }
                },
                "section": {
                  "nullable": true,
                  "required": true,
                  "schema": {
                    "type": "string"
                  }
                },
                "source_id": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "min_length": 1,
                    "type": "string"
                  }
                },
                "title": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "min_length": 1,
                    "type": "string"
                  }
                },
                "type": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "type": "string"
                  }
                }
              },
              "type": "object"
            },
            "GRAPH": {
              "additional_properties": false,
              "fields": {
                "excerpt": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "min_length": 1,
                    "type": "string"
                  }
                },
                "graph_revision": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "min_length": 1,
                    "type": "string"
                  }
                },
                "relation_id": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "min_length": 1,
                    "type": "string"
                  }
                },
                "source_id": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "min_length": 1,
                    "type": "string"
                  }
                },
                "title": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "min_length": 1,
                    "type": "string"
                  }
                },
                "type": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "type": "string"
                  }
                }
              },
              "type": "object"
            },
            "METROLOGY": {
              "additional_properties": false,
              "fields": {
                "excerpt": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "min_length": 1,
                    "type": "string"
                  }
                },
                "source_id": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "min_length": 1,
                    "type": "string"
                  }
                },
                "title": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "min_length": 1,
                    "type": "string"
                  }
                },
                "type": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "type": "string"
                  }
                }
              },
              "type": "object"
            },
            "TRACE": {
              "additional_properties": false,
              "fields": {
                "excerpt": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "min_length": 1,
                    "type": "string"
                  }
                },
                "source_id": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "min_length": 1,
                    "type": "string"
                  }
                },
                "title": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "min_length": 1,
                    "type": "string"
                  }
                },
                "type": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "type": "string"
                  }
                }
              },
              "type": "object"
            }
          }
        },
        "type": "array"
      }
    },
    "fault_code": {
      "nullable": true,
      "required": true,
      "schema": {
        "enum": [
          "FOC",
          "MFD",
          "OTH",
          "RFM",
          "TMD"
        ],
        "type": "string"
      }
    },
    "fault_color": {
      "nullable": false,
      "required": true,
      "schema": {
        "type": "null"
      }
    },
    "fault_name": {
      "nullable": false,
      "required": true,
      "schema": {
        "type": "null"
      }
    },
    "latency_ms": {
      "nullable": false,
      "required": true,
      "schema": {
        "minimum": 0.0,
        "type": "integer"
      }
    },
    "llm_model": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "predicted_fault_code": {
      "nullable": true,
      "required": true,
      "schema": {
        "enum": [
          "FOC",
          "MFD",
          "OTH",
          "RFM",
          "TMD"
        ],
        "type": "string"
      }
    },
    "recommended_action": {
      "nullable": true,
      "required": true,
      "schema": {
        "enum": [
          "EQP_HOLD",
          "MONITORING",
          "WARNING"
        ],
        "type": "string"
      }
    },
    "status": {
      "nullable": false,
      "required": true,
      "schema": {
        "enum": [
          "COMPLETED",
          "FAILED",
          "RUNNING",
          "WAITING_APPROVAL"
        ],
        "type": "string"
      }
    },
    "tools": {
      "nullable": false,
      "required": true,
      "schema": {
        "items": {
          "additional_properties": false,
          "fields": {
            "n": {
              "nullable": false,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            },
            "result_summary": {
              "nullable": false,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            },
            "s": {
              "nullable": false,
              "required": true,
              "schema": {
                "enum": [
                  "ERROR",
                  "SUCCESS",
                  "TIMEOUT"
                ],
                "type": "string"
              }
            },
            "status": {
              "nullable": false,
              "required": true,
              "schema": {
                "enum": [
                  "ERROR",
                  "SUCCESS",
                  "TIMEOUT"
                ],
                "type": "string"
              }
            },
            "tool_name": {
              "nullable": false,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            }
          },
          "type": "object"
        },
        "type": "array"
      }
    }
  },
  "type": "object"
}
```

### 5.15 `AgentRunItem`

```json
{
  "additional_properties": false,
  "fields": {
    "action_id": {
      "nullable": true,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "agent_run_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "alarm_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "alarm_source": {
      "nullable": false,
      "required": true,
      "schema": {
        "enum": [
          "R03",
          "SUMMARY",
          "TRACE"
        ],
        "type": "string"
      }
    },
    "approval_id": {
      "nullable": true,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "chamber": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "chamber_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "confidence": {
      "nullable": true,
      "required": true,
      "schema": {
        "maximum": 1,
        "minimum": 0,
        "type": "number"
      }
    },
    "created_at": {
      "nullable": false,
      "required": true,
      "schema": {
        "format": "date-time",
        "type": "string"
      }
    },
    "deliveries": {
      "nullable": false,
      "required": true,
      "schema": {
        "items": {
          "additional_properties": false,
          "fields": {
            "channel": {
              "nullable": false,
              "required": true,
              "schema": {
                "enum": [
                  "EMAIL",
                  "MES"
                ],
                "type": "string"
              }
            },
            "status": {
              "nullable": false,
              "required": true,
              "schema": {
                "enum": [
                  "BLOCKED",
                  "CANCELED",
                  "FAILED",
                  "SENDING",
                  "SENT",
                  "UNKNOWN",
                  "WAITING"
                ],
                "type": "string"
              }
            }
          },
          "type": "object"
        },
        "type": "array"
      }
    },
    "fault_code": {
      "nullable": true,
      "required": true,
      "schema": {
        "enum": [
          "FOC",
          "MFD",
          "OTH",
          "RFM",
          "TMD"
        ],
        "type": "string"
      }
    },
    "fault_color": {
      "nullable": false,
      "required": true,
      "schema": {
        "type": "null"
      }
    },
    "fault_name": {
      "nullable": false,
      "required": true,
      "schema": {
        "type": "null"
      }
    },
    "latency_ms": {
      "nullable": false,
      "required": true,
      "schema": {
        "minimum": 0,
        "type": "integer"
      }
    },
    "llm_model": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "predicted_fault_code": {
      "nullable": true,
      "required": true,
      "schema": {
        "enum": [
          "FOC",
          "MFD",
          "OTH",
          "RFM",
          "TMD"
        ],
        "type": "string"
      }
    },
    "recommended_action": {
      "nullable": true,
      "required": true,
      "schema": {
        "enum": [
          "EQP_HOLD",
          "MONITORING",
          "WARNING"
        ],
        "type": "string"
      }
    },
    "status": {
      "nullable": false,
      "required": true,
      "schema": {
        "enum": [
          "COMPLETED",
          "FAILED",
          "RUNNING",
          "WAITING_APPROVAL"
        ],
        "type": "string"
      }
    },
    "tools": {
      "nullable": false,
      "required": true,
      "schema": {
        "items": {
          "additional_properties": false,
          "fields": {
            "n": {
              "nullable": false,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            },
            "result_summary": {
              "nullable": false,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            },
            "s": {
              "nullable": false,
              "required": true,
              "schema": {
                "enum": [
                  "ERROR",
                  "SUCCESS",
                  "TIMEOUT"
                ],
                "type": "string"
              }
            },
            "status": {
              "nullable": false,
              "required": true,
              "schema": {
                "enum": [
                  "ERROR",
                  "SUCCESS",
                  "TIMEOUT"
                ],
                "type": "string"
              }
            },
            "tool_name": {
              "nullable": false,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            }
          },
          "type": "object"
        },
        "type": "array"
      }
    }
  },
  "type": "object"
}
```

### 5.16 `AlarmAskEvidence`

```json
{
  "additional_properties": false,
  "fields": {
    "excerpt": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "source_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "title": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "type": {
      "nullable": false,
      "required": true,
      "schema": {
        "type": "string"
      }
    }
  },
  "type": "object"
}
```

### 5.17 `AlarmEvidence`

```json
{
  "additional_properties": false,
  "fields": {
    "excerpt": {
      "nullable": false,
      "required": true,
      "schema": {
        "type": "string"
      }
    },
    "source_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "title": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "type": {
      "nullable": false,
      "required": true,
      "schema": {
        "enum": [
          "ALARM"
        ],
        "type": "string"
      }
    }
  },
  "type": "object"
}
```

### 5.18 `AlarmItem`

```json
{
  "additional_properties": false,
  "fields": {
    "action_code": {
      "nullable": true,
      "required": true,
      "schema": {
        "enum": [
          "EQP_HOLD",
          "MONITORING",
          "WARNING"
        ],
        "type": "string"
      }
    },
    "alarm_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "alarm_type": {
      "nullable": false,
      "required": true,
      "schema": {
        "enum": [
          "OOC",
          "OOS"
        ],
        "type": "string"
      }
    },
    "area": {
      "nullable": false,
      "required": true,
      "schema": {
        "enum": [
          "Etch",
          "Photo"
        ],
        "type": "string"
      }
    },
    "chamber": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "chamber_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "cl": {
      "nullable": true,
      "required": true,
      "schema": {
        "type": "number"
      }
    },
    "equipment": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "equipment_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "fault": {
      "nullable": true,
      "required": true,
      "schema": {
        "enum": [
          "FOC",
          "MFD",
          "OTH",
          "RFM",
          "TMD"
        ],
        "type": "string"
      }
    },
    "lcl": {
      "nullable": true,
      "required": true,
      "schema": {
        "type": "number"
      }
    },
    "lot": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "lot_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "mes": {
      "nullable": false,
      "required": true,
      "schema": {
        "type": "string"
      }
    },
    "mes_status": {
      "nullable": true,
      "required": true,
      "schema": {
        "enum": [
          "BLOCKED",
          "CANCELED",
          "FAILED",
          "SENDING",
          "SENT",
          "UNKNOWN",
          "WAITING"
        ],
        "type": "string"
      }
    },
    "notify": {
      "nullable": false,
      "required": true,
      "schema": {
        "type": "boolean"
      }
    },
    "notify_status": {
      "nullable": true,
      "required": true,
      "schema": {
        "enum": [
          "FAILED",
          "SENDING",
          "SENT",
          "UNKNOWN",
          "WAITING"
        ],
        "type": "string"
      }
    },
    "occurred_at": {
      "nullable": false,
      "required": true,
      "schema": {
        "format": "date-time",
        "type": "string"
      }
    },
    "parameter": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "parameter_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "predicted_fault_code": {
      "nullable": true,
      "required": true,
      "schema": {
        "enum": [
          "FOC",
          "MFD",
          "OTH",
          "RFM",
          "TMD"
        ],
        "type": "string"
      }
    },
    "recipe": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "recipe_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "recipe_step_no": {
      "nullable": false,
      "required": true,
      "schema": {
        "minimum": 1,
        "type": "integer"
      }
    },
    "rule_code": {
      "nullable": false,
      "required": true,
      "schema": {
        "enum": [
          "R03_CONSEC",
          "SUMMARY_OOC",
          "TRACE_OOS"
        ],
        "type": "string"
      }
    },
    "seq_no": {
      "nullable": true,
      "required": true,
      "schema": {
        "minimum": 0,
        "type": "integer"
      }
    },
    "source": {
      "nullable": false,
      "required": true,
      "schema": {
        "enum": [
          "R03",
          "SUMMARY",
          "TRACE"
        ],
        "type": "string"
      }
    },
    "statistic_type": {
      "nullable": true,
      "required": true,
      "schema": {
        "type": "string"
      }
    },
    "step_no": {
      "nullable": false,
      "required": true,
      "schema": {
        "minimum": 1,
        "type": "integer"
      }
    },
    "ucl": {
      "nullable": true,
      "required": true,
      "schema": {
        "type": "number"
      }
    },
    "value": {
      "nullable": true,
      "required": true,
      "schema": {
        "type": "number"
      }
    },
    "wafer": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "wafer_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    }
  },
  "rules": [
    "source TRACE requires rule_code TRACE_OOS",
    "source SUMMARY requires rule_code SUMMARY_OOC",
    "source R03 requires alarm_type OOS, rule_code R03_CONSEC, and value null",
    "compatibility aliases derive only from canonical fields"
  ],
  "type": "object"
}
```

### 5.19 `AlarmRef`

```json
{
  "additional_properties": false,
  "fields": {
    "alarm_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "source": {
      "nullable": false,
      "required": true,
      "schema": {
        "enum": [
          "R03",
          "SUMMARY",
          "TRACE"
        ],
        "type": "string"
      }
    }
  },
  "type": "object"
}
```

### 5.20 `AlarmSource`

```json
{
  "enum": [
    "R03",
    "SUMMARY",
    "TRACE"
  ],
  "type": "string"
}
```

### 5.21 `AnalysisQueryRequest`

```json
{
  "additional_properties": false,
  "fields": {
    "question": {
      "nullable": false,
      "required": true,
      "schema": {
        "max_length": 1000,
        "min_length": 1,
        "type": "string"
      }
    }
  },
  "type": "object"
}
```

### 5.22 `AnalysisQueryResponse`

```json
{
  "additional_properties": false,
  "fields": {
    "columns": {
      "nullable": false,
      "required": true,
      "schema": {
        "items": {
          "type": "string"
        },
        "type": "array"
      }
    },
    "cross_check": {
      "nullable": true,
      "required": false,
      "schema": {
        "additional_properties": false,
        "fields": {
          "cypher": {
            "nullable": true,
            "required": false,
            "schema": {
              "type": "string"
            }
          },
          "status": {
            "nullable": false,
            "required": true,
            "schema": {
              "enum": [
                "MATCH",
                "MISMATCH",
                "SKIPPED"
              ],
              "type": "string"
            }
          },
          "summary": {
            "nullable": true,
            "required": false,
            "schema": {
              "type": "string"
            }
          }
        },
        "type": "object"
      }
    },
    "error_msg": {
      "nullable": true,
      "required": false,
      "schema": {
        "type": "string"
      }
    },
    "generated_sql": {
      "nullable": true,
      "required": false,
      "schema": {
        "type": "string"
      }
    },
    "group_by": {
      "nullable": false,
      "required": true,
      "schema": {
        "items": {
          "type": "string"
        },
        "type": "array"
      }
    },
    "is_rejected": {
      "nullable": false,
      "required": true,
      "schema": {
        "type": "boolean"
      }
    },
    "is_valid": {
      "nullable": false,
      "required": true,
      "schema": {
        "type": "boolean"
      }
    },
    "latency_ms": {
      "nullable": false,
      "required": true,
      "schema": {
        "minimum": 0.0,
        "type": "integer"
      }
    },
    "metric": {
      "nullable": true,
      "required": false,
      "schema": {
        "additional_properties": false,
        "fields": {
          "column": {
            "nullable": true,
            "required": false,
            "schema": {
              "type": "string"
            }
          },
          "p": {
            "nullable": true,
            "required": false,
            "schema": {
              "maximum": 100.0,
              "minimum": 0.0,
              "type": "number"
            }
          },
          "type": {
            "nullable": false,
            "required": true,
            "schema": {
              "enum": [
                "count",
                "max",
                "mean",
                "median",
                "min",
                "percentile",
                "ratio",
                "std",
                "sum"
              ],
              "type": "string"
            }
          }
        },
        "type": "object"
      }
    },
    "metric_result": {
      "nullable": false,
      "required": false,
      "schema": {
        "type": "union",
        "variants": [
          {
            "type": "integer"
          },
          {
            "type": "number"
          },
          {
            "items": {
              "additional_properties": false,
              "fields": {
                "group": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "additional_properties": true,
                    "fields": {},
                    "type": "object"
                  }
                },
                "value": {
                  "nullable": false,
                  "required": false,
                  "schema": {
                    "type": "union",
                    "variants": [
                      {
                        "type": "integer"
                      },
                      {
                        "type": "number"
                      },
                      {
                        "type": "null"
                      }
                    ]
                  }
                }
              },
              "type": "object"
            },
            "type": "array"
          },
          {
            "type": "null"
          }
        ]
      }
    },
    "nl_query_log_id": {
      "nullable": true,
      "required": false,
      "schema": {
        "minimum": 1.0,
        "type": "integer"
      }
    },
    "question": {
      "nullable": false,
      "required": true,
      "schema": {
        "type": "string"
      }
    },
    "reject_reason": {
      "nullable": true,
      "required": false,
      "schema": {
        "type": "string"
      }
    },
    "row_count": {
      "nullable": false,
      "required": true,
      "schema": {
        "minimum": 0.0,
        "type": "integer"
      }
    },
    "rows": {
      "nullable": false,
      "required": true,
      "schema": {
        "items": {
          "additional_properties": true,
          "fields": {},
          "type": "object"
        },
        "type": "array"
      }
    },
    "visualization": {
      "nullable": true,
      "required": false,
      "schema": {
        "additional_properties": false,
        "fields": {
          "chart_type": {
            "nullable": false,
            "required": true,
            "schema": {
              "enum": [
                "bar",
                "histogram",
                "line",
                "table"
              ],
              "type": "string"
            }
          },
          "x": {
            "nullable": true,
            "required": false,
            "schema": {
              "type": "string"
            }
          },
          "y": {
            "nullable": true,
            "required": false,
            "schema": {
              "type": "string"
            }
          }
        },
        "type": "object"
      }
    }
  },
  "type": "object"
}
```

### 5.23 `ApprovalDecisionRequest`

```json
{
  "additional_properties": false,
  "fields": {
    "decided_by": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "decision": {
      "nullable": false,
      "required": true,
      "schema": {
        "enum": [
          "APPROVED",
          "REJECTED"
        ],
        "type": "string"
      }
    },
    "decision_comment": {
      "nullable": true,
      "required": false,
      "schema": {
        "max_length": 1000,
        "min_length": 1,
        "type": "string"
      }
    }
  },
  "type": "object"
}
```

### 5.24 `ApprovalItem`

```json
{
  "additional_properties": false,
  "fields": {
    "action_code": {
      "nullable": false,
      "required": true,
      "schema": {
        "enum": [
          "EQP_HOLD",
          "MONITORING",
          "WARNING"
        ],
        "type": "string"
      }
    },
    "action_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "agent_run_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "approval_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "approved_at": {
      "nullable": true,
      "required": true,
      "schema": {
        "format": "date-time",
        "type": "string"
      }
    },
    "approved_by": {
      "nullable": true,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "chamber": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "chamber_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "created_at": {
      "nullable": false,
      "required": true,
      "schema": {
        "format": "date-time",
        "type": "string"
      }
    },
    "decided_at": {
      "nullable": true,
      "required": true,
      "schema": {
        "format": "date-time",
        "type": "string"
      }
    },
    "decided_by": {
      "nullable": true,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "decision_comment": {
      "nullable": true,
      "required": true,
      "schema": {
        "max_length": 1000,
        "min_length": 1,
        "type": "string"
      }
    },
    "equipment": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "equipment_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "fault_code": {
      "nullable": false,
      "required": true,
      "schema": {
        "enum": [
          "FOC",
          "MFD",
          "OTH",
          "RFM",
          "TMD"
        ],
        "type": "string"
      }
    },
    "lot": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "lot_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "predicted_fault_code": {
      "nullable": false,
      "required": true,
      "schema": {
        "enum": [
          "FOC",
          "MFD",
          "OTH",
          "RFM",
          "TMD"
        ],
        "type": "string"
      }
    },
    "reason": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "status": {
      "nullable": false,
      "required": true,
      "schema": {
        "enum": [
          "APPROVED",
          "PENDING",
          "REJECTED"
        ],
        "type": "string"
      }
    }
  },
  "rules": [
    "PENDING requires all decision fields null",
    "APPROVED or REJECTED requires decided_by and decided_at",
    "only EQP_HOLD actions have approvals"
  ],
  "type": "object"
}
```

### 5.25 `AskDocumentEvidenceAlias`

```json
{
  "additional_properties": false,
  "fields": {
    "chunk_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "doc_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "document_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "section": {
      "nullable": true,
      "required": true,
      "schema": {
        "type": "string"
      }
    }
  },
  "type": "object"
}
```

### 5.26 `AskToolItem`

```json
{
  "additional_properties": false,
  "fields": {
    "name": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "result": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "result_summary": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "status": {
      "nullable": false,
      "required": true,
      "schema": {
        "enum": [
          "ERROR",
          "SUCCESS",
          "TIMEOUT"
        ],
        "type": "string"
      }
    },
    "tool_name": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    }
  },
  "type": "object"
}
```

### 5.27 `AuditLogItem`

```json
{
  "additional_properties": false,
  "fields": {
    "actor": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "actor_id": {
      "nullable": true,
      "required": true,
      "schema": {
        "type": "string"
      }
    },
    "actor_type": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "after": {
      "nullable": true,
      "required": true,
      "schema": {
        "additional_properties": true,
        "fields": {},
        "type": "object"
      }
    },
    "at": {
      "nullable": false,
      "required": true,
      "schema": {
        "format": "date-time",
        "type": "string"
      }
    },
    "audit_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "minimum": 1,
        "type": "integer"
      }
    },
    "before": {
      "nullable": true,
      "required": true,
      "schema": {
        "additional_properties": true,
        "fields": {},
        "type": "object"
      }
    },
    "detail": {
      "nullable": true,
      "required": true,
      "schema": {
        "type": "string"
      }
    },
    "entity": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "entity_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "entity_type": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "event": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "event_type": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "occurred_at": {
      "nullable": false,
      "required": true,
      "schema": {
        "format": "date-time",
        "type": "string"
      }
    }
  },
  "type": "object"
}
```

### 5.28 `AuditLogPageResponse`

```json
{
  "additional_properties": true,
  "fields": {
    "event_type_counts": {
      "nullable": false,
      "required": true,
      "schema": {
        "additional_properties": {
          "type": "integer"
        },
        "fields": {},
        "type": "object"
      }
    },
    "event_types": {
      "nullable": false,
      "required": true,
      "schema": {
        "items": {
          "type": "string"
        },
        "type": "array"
      }
    },
    "items": {
      "nullable": false,
      "required": true,
      "schema": {
        "items": {
          "additional_properties": true,
          "fields": {
            "actor": {
              "nullable": false,
              "required": true,
              "schema": {
                "type": "string"
              }
            },
            "actor_id": {
              "nullable": true,
              "required": true,
              "schema": {
                "type": "string"
              }
            },
            "actor_type": {
              "nullable": false,
              "required": true,
              "schema": {
                "type": "string"
              }
            },
            "after": {
              "nullable": true,
              "required": true,
              "schema": {
                "additional_properties": true,
                "fields": {},
                "type": "object"
              }
            },
            "at": {
              "nullable": false,
              "required": true,
              "schema": {
                "format": "date-time",
                "type": "string"
              }
            },
            "audit_id": {
              "nullable": false,
              "required": true,
              "schema": {
                "type": "integer"
              }
            },
            "before": {
              "nullable": true,
              "required": true,
              "schema": {
                "additional_properties": true,
                "fields": {},
                "type": "object"
              }
            },
            "detail": {
              "nullable": true,
              "required": true,
              "schema": {
                "type": "string"
              }
            },
            "entity": {
              "nullable": false,
              "required": true,
              "schema": {
                "type": "string"
              }
            },
            "entity_id": {
              "nullable": false,
              "required": true,
              "schema": {
                "type": "string"
              }
            },
            "entity_type": {
              "nullable": false,
              "required": true,
              "schema": {
                "type": "string"
              }
            },
            "event": {
              "nullable": false,
              "required": true,
              "schema": {
                "type": "string"
              }
            },
            "event_type": {
              "nullable": false,
              "required": true,
              "schema": {
                "type": "string"
              }
            },
            "occurred_at": {
              "nullable": false,
              "required": true,
              "schema": {
                "format": "date-time",
                "type": "string"
              }
            }
          },
          "type": "object"
        },
        "type": "array"
      }
    },
    "page": {
      "nullable": false,
      "required": true,
      "schema": {
        "type": "integer"
      }
    },
    "size": {
      "nullable": false,
      "required": true,
      "schema": {
        "type": "integer"
      }
    },
    "total": {
      "nullable": false,
      "required": true,
      "schema": {
        "type": "integer"
      }
    }
  },
  "type": "object"
}
```

### 5.29 `AutoToolCallItem`

```json
{
  "additional_properties": false,
  "fields": {
    "n": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "result_summary": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "s": {
      "nullable": false,
      "required": true,
      "schema": {
        "enum": [
          "ERROR",
          "SUCCESS",
          "TIMEOUT"
        ],
        "type": "string"
      }
    },
    "status": {
      "nullable": false,
      "required": true,
      "schema": {
        "enum": [
          "ERROR",
          "SUCCESS",
          "TIMEOUT"
        ],
        "type": "string"
      }
    },
    "tool_name": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    }
  },
  "type": "object"
}
```

### 5.30 `ChamberGraphResponse`

```json
{
  "additional_properties": false,
  "fields": {
    "context": {
      "nullable": false,
      "required": true,
      "schema": {
        "additional_properties": false,
        "fields": {
          "adjacent_process_step_ids": {
            "nullable": false,
            "required": true,
            "schema": {
              "items": {
                "type": "string"
              },
              "type": "array"
            }
          },
          "area": {
            "nullable": false,
            "required": true,
            "schema": {
              "enum": [
                "Etch",
                "Photo"
              ],
              "type": "string"
            }
          },
          "chamber_id": {
            "nullable": false,
            "required": true,
            "schema": {
              "min_length": 1,
              "type": "string"
            }
          },
          "equipment_id": {
            "nullable": false,
            "required": true,
            "schema": {
              "min_length": 1,
              "type": "string"
            }
          },
          "model_code": {
            "nullable": false,
            "required": true,
            "schema": {
              "min_length": 1,
              "type": "string"
            }
          },
          "parameter_ids": {
            "nullable": false,
            "required": true,
            "schema": {
              "items": {
                "type": "string"
              },
              "type": "array"
            }
          },
          "process_step_id": {
            "nullable": false,
            "required": true,
            "schema": {
              "min_length": 1,
              "type": "string"
            }
          },
          "relation_ids": {
            "nullable": false,
            "required": true,
            "schema": {
              "items": {
                "type": "string"
              },
              "type": "array"
            }
          }
        },
        "type": "object"
      }
    },
    "graph_revision": {
      "nullable": false,
      "required": true,
      "schema": {
        "pattern": "^[0-9a-f]{64}$",
        "type": "string"
      }
    },
    "node_count": {
      "nullable": false,
      "required": true,
      "schema": {
        "minimum": 0,
        "type": "integer"
      }
    },
    "nodes": {
      "nullable": false,
      "required": true,
      "schema": {
        "items": {
          "additional_properties": false,
          "fields": {
            "business_id": {
              "nullable": false,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            },
            "label": {
              "nullable": false,
              "required": true,
              "schema": {
                "enum": [
                  "Area",
                  "Chamber",
                  "Equipment",
                  "EquipmentModel",
                  "Parameter",
                  "ProcessStep"
                ],
                "type": "string"
              }
            },
            "name": {
              "nullable": false,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            },
            "node_id": {
              "nullable": false,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            },
            "properties": {
              "nullable": false,
              "required": true,
              "schema": {
                "additional_properties": true,
                "fields": {},
                "type": "object"
              }
            }
          },
          "type": "object"
        },
        "type": "array"
      }
    },
    "relationship_count": {
      "nullable": false,
      "required": true,
      "schema": {
        "minimum": 0,
        "type": "integer"
      }
    },
    "relationships": {
      "nullable": false,
      "required": true,
      "schema": {
        "items": {
          "additional_properties": false,
          "fields": {
            "from_node_id": {
              "nullable": false,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            },
            "relation_id": {
              "nullable": false,
              "required": true,
              "schema": {
                "pattern": "^REL-[0-9a-f]{20}$",
                "type": "string"
              }
            },
            "to_node_id": {
              "nullable": false,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            },
            "type": {
              "nullable": false,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            }
          },
          "type": "object"
        },
        "type": "array"
      }
    }
  },
  "rules": [
    "node_count equals len(nodes)",
    "relationship_count equals len(relationships)"
  ],
  "type": "object"
}
```

### 5.31 `ChamberRelationResponse`

```json
{
  "additional_properties": false,
  "fields": {
    "graph_revision": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "nodes": {
      "nullable": false,
      "required": true,
      "schema": {
        "items": {
          "additional_properties": false,
          "fields": {
            "business_id": {
              "nullable": false,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            },
            "display_name": {
              "nullable": false,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            },
            "id": {
              "nullable": false,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            },
            "label": {
              "nullable": false,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            },
            "properties": {
              "nullable": false,
              "required": true,
              "schema": {
                "additional_properties": true,
                "fields": {},
                "type": "object"
              }
            }
          },
          "type": "object"
        },
        "type": "array"
      }
    },
    "relationships": {
      "nullable": false,
      "required": true,
      "schema": {
        "items": {
          "additional_properties": false,
          "fields": {
            "id": {
              "nullable": false,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            },
            "source": {
              "nullable": false,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            },
            "target": {
              "nullable": false,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            },
            "type": {
              "nullable": false,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            }
          },
          "type": "object"
        },
        "type": "array"
      }
    },
    "root_node_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    }
  },
  "type": "object"
}
```

### 5.32 `ChartType`

```json
{
  "enum": [
    "bar",
    "histogram",
    "line",
    "table"
  ],
  "type": "string"
}
```

### 5.33 `ChatToolCallItem`

```json
{
  "additional_properties": false,
  "fields": {
    "name": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "result": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "result_summary": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "status": {
      "nullable": false,
      "required": true,
      "schema": {
        "enum": [
          "ERROR",
          "SUCCESS",
          "TIMEOUT"
        ],
        "type": "string"
      }
    },
    "tool_name": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    }
  },
  "type": "object"
}
```

### 5.34 `CrossCheck`

```json
{
  "additional_properties": false,
  "fields": {
    "cypher": {
      "nullable": true,
      "required": false,
      "schema": {
        "type": "string"
      }
    },
    "status": {
      "nullable": false,
      "required": true,
      "schema": {
        "enum": [
          "MATCH",
          "MISMATCH",
          "SKIPPED"
        ],
        "type": "string"
      }
    },
    "summary": {
      "nullable": true,
      "required": false,
      "schema": {
        "type": "string"
      }
    }
  },
  "type": "object"
}
```

### 5.35 `DeliveryCallbackRequest`

```json
{
  "additional_properties": false,
  "fields": {
    "channel": {
      "nullable": false,
      "required": true,
      "schema": {
        "enum": [
          "EMAIL",
          "MES_MOCK"
        ],
        "type": "string"
      }
    },
    "completed_at": {
      "nullable": false,
      "required": true,
      "schema": {
        "format": "date-time",
        "type": "string"
      }
    },
    "error_code": {
      "nullable": true,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "event_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "provider_message_id": {
      "nullable": true,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "request_hash": {
      "nullable": false,
      "required": true,
      "schema": {
        "max_length": 64,
        "min_length": 64,
        "pattern": "^[0-9a-f]{64}$",
        "type": "string"
      }
    },
    "status": {
      "nullable": false,
      "required": true,
      "schema": {
        "enum": [
          "FAILED",
          "SENT"
        ],
        "type": "string"
      }
    }
  },
  "rules": [
    "SENT requires provider_message_id non-null and error_code null",
    "FAILED requires error_code non-null and permits provider_message_id null"
  ],
  "type": "object"
}
```

### 5.36 `DeliveryChannel`

```json
{
  "enum": [
    "EMAIL",
    "MES_MOCK"
  ],
  "type": "string"
}
```

### 5.37 `DeliveryResult`

```json
{
  "additional_properties": false,
  "fields": {
    "action_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "channel": {
      "nullable": false,
      "required": true,
      "schema": {
        "enum": [
          "EMAIL",
          "MES_MOCK"
        ],
        "type": "string"
      }
    },
    "completed_at": {
      "nullable": false,
      "required": true,
      "schema": {
        "format": "date-time",
        "type": "string"
      }
    },
    "duplicate": {
      "nullable": false,
      "required": true,
      "schema": {
        "type": "boolean"
      }
    },
    "error_code": {
      "nullable": true,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "provider_message_id": {
      "nullable": true,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "request_hash": {
      "nullable": false,
      "required": true,
      "schema": {
        "max_length": 64,
        "min_length": 64,
        "pattern": "^[0-9a-f]{64}$",
        "type": "string"
      }
    },
    "status": {
      "nullable": false,
      "required": true,
      "schema": {
        "enum": [
          "FAILED",
          "SENT"
        ],
        "type": "string"
      }
    }
  },
  "rules": [
    "SENT requires provider_message_id non-null and error_code null",
    "FAILED requires error_code non-null and permits provider_message_id null"
  ],
  "type": "object"
}
```

### 5.38 `DeliveryStatus`

```json
{
  "enum": [
    "BLOCKED",
    "CANCELED",
    "FAILED",
    "SENDING",
    "SENT",
    "UNKNOWN",
    "WAITING"
  ],
  "type": "string"
}
```

### 5.39 `DocumentAskEvidence`

```json
{
  "additional_properties": false,
  "fields": {
    "chunk_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "document_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "excerpt": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "section": {
      "nullable": true,
      "required": true,
      "schema": {
        "type": "string"
      }
    },
    "source_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "title": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "type": {
      "nullable": false,
      "required": true,
      "schema": {
        "type": "string"
      }
    }
  },
  "type": "object"
}
```

### 5.40 `DocumentChunkItem`

```json
{
  "additional_properties": false,
  "fields": {
    "chunk_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "chunk_seq": {
      "nullable": false,
      "required": true,
      "schema": {
        "minimum": 0.0,
        "type": "integer"
      }
    },
    "content": {
      "nullable": false,
      "required": true,
      "schema": {
        "type": "string"
      }
    },
    "section_title": {
      "nullable": true,
      "required": false,
      "schema": {
        "type": "string"
      }
    }
  },
  "type": "object"
}
```

### 5.41 `DocumentDetailResponse`

```json
{
  "additional_properties": false,
  "fields": {
    "chunks": {
      "nullable": false,
      "required": true,
      "schema": {
        "items": {
          "additional_properties": false,
          "fields": {
            "chunk_id": {
              "nullable": false,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            },
            "chunk_seq": {
              "nullable": false,
              "required": true,
              "schema": {
                "minimum": 0.0,
                "type": "integer"
              }
            },
            "content": {
              "nullable": false,
              "required": true,
              "schema": {
                "type": "string"
              }
            },
            "section_title": {
              "nullable": true,
              "required": false,
              "schema": {
                "type": "string"
              }
            }
          },
          "type": "object"
        },
        "type": "array"
      }
    },
    "doc_type": {
      "nullable": true,
      "required": false,
      "schema": {
        "enum": [
          "MANUAL",
          "SPEC",
          "TROUBLESHOOT"
        ],
        "type": "string"
      }
    },
    "document_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "model_code": {
      "nullable": true,
      "required": false,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "source_path": {
      "nullable": true,
      "required": false,
      "schema": {
        "type": "string"
      }
    },
    "title": {
      "nullable": false,
      "required": true,
      "schema": {
        "type": "string"
      }
    },
    "version": {
      "nullable": true,
      "required": false,
      "schema": {
        "type": "string"
      }
    }
  },
  "type": "object"
}
```

### 5.42 `DocumentEvidence`

```json
{
  "additional_properties": false,
  "fields": {
    "chunk_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "document_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "excerpt": {
      "nullable": false,
      "required": true,
      "schema": {
        "type": "string"
      }
    },
    "section": {
      "nullable": true,
      "required": true,
      "schema": {
        "type": "string"
      }
    },
    "source_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "title": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "type": {
      "nullable": false,
      "required": true,
      "schema": {
        "enum": [
          "DOCUMENT"
        ],
        "type": "string"
      }
    }
  },
  "type": "object"
}
```

### 5.43 `DocumentHit`

```json
{
  "additional_properties": false,
  "fields": {
    "chunk_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "pattern": "^.+:cs2:[0-9]{4}$",
        "type": "string"
      }
    },
    "content": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "doc_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "document_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "model_code": {
      "nullable": true,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "score": {
      "nullable": false,
      "required": true,
      "schema": {
        "maximum": 1,
        "minimum": -1,
        "type": "number"
      }
    },
    "section": {
      "nullable": true,
      "required": true,
      "schema": {
        "type": "string"
      }
    },
    "title": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    }
  },
  "type": "object"
}
```

### 5.44 `DocumentSearchRequest`

```json
{
  "additional_properties": false,
  "fields": {
    "model_code": {
      "nullable": true,
      "required": false,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "query": {
      "nullable": false,
      "required": true,
      "schema": {
        "max_length": 1000,
        "min_length": 1,
        "type": "string"
      }
    },
    "top_k": {
      "nullable": false,
      "required": false,
      "schema": {
        "default": 4,
        "maximum": 10,
        "minimum": 1,
        "type": "integer"
      }
    }
  },
  "type": "object"
}
```

### 5.45 `DocumentType`

```json
{
  "enum": [
    "MANUAL",
    "SPEC",
    "TROUBLESHOOT"
  ],
  "type": "string"
}
```

### 5.46 `ErrorCode`

```json
{
  "enum": [
    "APPROVAL_ALREADY_DECIDED",
    "DEPENDENCY_NOT_READY",
    "IDEMPOTENCY_CONFLICT",
    "INCIDENT_ALREADY_PROCESSED",
    "INCIDENT_ALREADY_RUNNING",
    "INTERNAL_ERROR",
    "LEGACY_APPROVAL_NOT_LINKED",
    "LLM_NOT_READY",
    "MODEL_NOT_READY",
    "POLICY_REJECTED",
    "RESOURCE_NOT_FOUND",
    "UNAUTHORIZED",
    "VALIDATION_ERROR"
  ],
  "type": "string"
}
```

### 5.47 `ErrorResponse`

```json
{
  "additional_properties": false,
  "fields": {
    "code": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "details": {
      "nullable": false,
      "required": true,
      "schema": {
        "additional_properties": true,
        "fields": {},
        "type": "object"
      }
    },
    "message": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    }
  },
  "type": "object"
}
```

### 5.48 `EvaluationListResponse`

```json
{
  "additional_properties": false,
  "fields": {
    "items": {
      "nullable": false,
      "required": true,
      "schema": {
        "items": {
          "additional_properties": false,
          "fields": {
            "accuracy": {
              "nullable": false,
              "required": true,
              "schema": {
                "maximum": 1.0,
                "minimum": 0.0,
                "type": "number"
              }
            },
            "correct": {
              "nullable": false,
              "required": true,
              "schema": {
                "minimum": 0,
                "type": "integer"
              }
            },
            "defense_passed": {
              "nullable": false,
              "required": true,
              "schema": {
                "minimum": 0,
                "type": "integer"
              }
            },
            "defense_total": {
              "nullable": false,
              "required": true,
              "schema": {
                "minimum": 0,
                "type": "integer"
              }
            },
            "executed_at": {
              "nullable": false,
              "required": true,
              "schema": {
                "format": "date-time",
                "type": "string"
              }
            },
            "items": {
              "nullable": false,
              "required": true,
              "schema": {
                "items": {
                  "additional_properties": false,
                  "fields": {
                    "actual_result": {
                      "nullable": true,
                      "required": false,
                      "schema": {
                        "type": "any"
                      }
                    },
                    "actual_visualization": {
                      "nullable": true,
                      "required": false,
                      "schema": {
                        "additional_properties": false,
                        "fields": {
                          "chart_type": {
                            "nullable": false,
                            "required": true,
                            "schema": {
                              "enum": [
                                "bar",
                                "histogram",
                                "line",
                                "table"
                              ],
                              "type": "string"
                            }
                          },
                          "x": {
                            "nullable": true,
                            "required": false,
                            "schema": {
                              "type": "string"
                            }
                          },
                          "y": {
                            "nullable": true,
                            "required": false,
                            "schema": {
                              "type": "string"
                            }
                          }
                        },
                        "type": "object"
                      }
                    },
                    "attempt_count": {
                      "nullable": false,
                      "required": true,
                      "schema": {
                        "minimum": 0,
                        "type": "integer"
                      }
                    },
                    "case_id": {
                      "nullable": false,
                      "required": true,
                      "schema": {
                        "min_length": 1,
                        "type": "string"
                      }
                    },
                    "case_type": {
                      "nullable": false,
                      "required": true,
                      "schema": {
                        "enum": [
                          "DEFENSE",
                          "GOLD"
                        ],
                        "type": "string"
                      }
                    },
                    "expected_result": {
                      "nullable": true,
                      "required": false,
                      "schema": {
                        "type": "any"
                      }
                    },
                    "expected_visualization": {
                      "nullable": true,
                      "required": false,
                      "schema": {
                        "additional_properties": false,
                        "fields": {
                          "chart_type": {
                            "nullable": false,
                            "required": true,
                            "schema": {
                              "enum": [
                                "bar",
                                "histogram",
                                "line",
                                "table"
                              ],
                              "type": "string"
                            }
                          },
                          "x": {
                            "nullable": true,
                            "required": false,
                            "schema": {
                              "type": "string"
                            }
                          },
                          "y": {
                            "nullable": true,
                            "required": false,
                            "schema": {
                              "type": "string"
                            }
                          }
                        },
                        "type": "object"
                      }
                    },
                    "generated_sql": {
                      "nullable": true,
                      "required": false,
                      "schema": {
                        "type": "string"
                      }
                    },
                    "latency_ms": {
                      "nullable": true,
                      "required": false,
                      "schema": {
                        "minimum": 0,
                        "type": "integer"
                      }
                    },
                    "passed": {
                      "nullable": false,
                      "required": true,
                      "schema": {
                        "type": "boolean"
                      }
                    },
                    "question": {
                      "nullable": true,
                      "required": false,
                      "schema": {
                        "type": "string"
                      }
                    },
                    "reason": {
                      "nullable": true,
                      "required": false,
                      "schema": {
                        "type": "string"
                      }
                    }
                  },
                  "type": "object"
                },
                "type": "array"
              }
            },
            "model": {
              "nullable": false,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            },
            "prompt_version": {
              "nullable": false,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            },
            "provider": {
              "nullable": false,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            },
            "run_id": {
              "nullable": false,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            },
            "temperature": {
              "nullable": false,
              "required": true,
              "schema": {
                "type": "number"
              }
            },
            "total": {
              "nullable": false,
              "required": true,
              "schema": {
                "minimum": 0,
                "type": "integer"
              }
            }
          },
          "type": "object"
        },
        "type": "array"
      }
    },
    "page": {
      "nullable": false,
      "required": true,
      "schema": {
        "minimum": 1,
        "type": "integer"
      }
    },
    "size": {
      "nullable": false,
      "required": true,
      "schema": {
        "maximum": 100,
        "minimum": 1,
        "type": "integer"
      }
    },
    "total": {
      "nullable": false,
      "required": true,
      "schema": {
        "minimum": 0,
        "type": "integer"
      }
    }
  },
  "type": "object"
}
```

### 5.49 `EvidenceItem`

```json
{
  "discriminator": "type",
  "type": "discriminated_union",
  "variants": {
    "ALARM": {
      "additional_properties": false,
      "fields": {
        "excerpt": {
          "nullable": false,
          "required": true,
          "schema": {
            "type": "string"
          }
        },
        "source_id": {
          "nullable": false,
          "required": true,
          "schema": {
            "min_length": 1,
            "type": "string"
          }
        },
        "title": {
          "nullable": false,
          "required": true,
          "schema": {
            "min_length": 1,
            "type": "string"
          }
        },
        "type": {
          "nullable": false,
          "required": true,
          "schema": {
            "enum": [
              "ALARM"
            ],
            "type": "string"
          }
        }
      },
      "type": "object"
    },
    "DOCUMENT": {
      "additional_properties": false,
      "fields": {
        "chunk_id": {
          "nullable": false,
          "required": true,
          "schema": {
            "min_length": 1,
            "type": "string"
          }
        },
        "document_id": {
          "nullable": false,
          "required": true,
          "schema": {
            "min_length": 1,
            "type": "string"
          }
        },
        "excerpt": {
          "nullable": false,
          "required": true,
          "schema": {
            "type": "string"
          }
        },
        "section": {
          "nullable": true,
          "required": true,
          "schema": {
            "type": "string"
          }
        },
        "source_id": {
          "nullable": false,
          "required": true,
          "schema": {
            "min_length": 1,
            "type": "string"
          }
        },
        "title": {
          "nullable": false,
          "required": true,
          "schema": {
            "min_length": 1,
            "type": "string"
          }
        },
        "type": {
          "nullable": false,
          "required": true,
          "schema": {
            "enum": [
              "DOCUMENT"
            ],
            "type": "string"
          }
        }
      },
      "type": "object"
    },
    "GRAPH": {
      "additional_properties": false,
      "fields": {
        "excerpt": {
          "nullable": false,
          "required": true,
          "schema": {
            "type": "string"
          }
        },
        "graph_revision": {
          "nullable": false,
          "required": true,
          "schema": {
            "pattern": "^[0-9a-f]{64}$",
            "type": "string"
          }
        },
        "relation_id": {
          "nullable": false,
          "required": true,
          "schema": {
            "pattern": "^REL-[0-9a-f]{20}$",
            "type": "string"
          }
        },
        "source_id": {
          "nullable": false,
          "required": true,
          "schema": {
            "min_length": 1,
            "type": "string"
          }
        },
        "title": {
          "nullable": false,
          "required": true,
          "schema": {
            "min_length": 1,
            "type": "string"
          }
        },
        "type": {
          "nullable": false,
          "required": true,
          "schema": {
            "enum": [
              "GRAPH"
            ],
            "type": "string"
          }
        }
      },
      "type": "object"
    },
    "METROLOGY": {
      "additional_properties": false,
      "fields": {
        "excerpt": {
          "nullable": false,
          "required": true,
          "schema": {
            "type": "string"
          }
        },
        "source_id": {
          "nullable": false,
          "required": true,
          "schema": {
            "min_length": 1,
            "type": "string"
          }
        },
        "title": {
          "nullable": false,
          "required": true,
          "schema": {
            "min_length": 1,
            "type": "string"
          }
        },
        "type": {
          "nullable": false,
          "required": true,
          "schema": {
            "enum": [
              "METROLOGY"
            ],
            "type": "string"
          }
        }
      },
      "rules": [
        "metrology.alarm_result is forbidden"
      ],
      "type": "object"
    },
    "TRACE": {
      "additional_properties": false,
      "fields": {
        "excerpt": {
          "nullable": false,
          "required": true,
          "schema": {
            "type": "string"
          }
        },
        "source_id": {
          "nullable": false,
          "required": true,
          "schema": {
            "min_length": 1,
            "type": "string"
          }
        },
        "title": {
          "nullable": false,
          "required": true,
          "schema": {
            "min_length": 1,
            "type": "string"
          }
        },
        "type": {
          "nullable": false,
          "required": true,
          "schema": {
            "enum": [
              "TRACE"
            ],
            "type": "string"
          }
        }
      },
      "type": "object"
    }
  }
}
```

### 5.50 `FaultHypothesis`

```json
{
  "enum": [
    "FOC",
    "MFD",
    "OTH",
    "RFM",
    "TMD"
  ],
  "type": "string"
}
```

### 5.51 `GraphAskEvidence`

```json
{
  "additional_properties": false,
  "fields": {
    "excerpt": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "graph_revision": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "relation_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "source_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "title": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "type": {
      "nullable": false,
      "required": true,
      "schema": {
        "type": "string"
      }
    }
  },
  "type": "object"
}
```

### 5.52 `GraphContext`

```json
{
  "additional_properties": false,
  "fields": {
    "adjacent_process_step_ids": {
      "nullable": false,
      "required": true,
      "schema": {
        "items": {
          "type": "string"
        },
        "type": "array"
      }
    },
    "area": {
      "nullable": false,
      "required": true,
      "schema": {
        "enum": [
          "Etch",
          "Photo"
        ],
        "type": "string"
      }
    },
    "chamber_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "equipment_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "model_code": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "parameter_ids": {
      "nullable": false,
      "required": true,
      "schema": {
        "items": {
          "type": "string"
        },
        "type": "array"
      }
    },
    "process_step_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "relation_ids": {
      "nullable": false,
      "required": true,
      "schema": {
        "items": {
          "type": "string"
        },
        "type": "array"
      }
    }
  },
  "type": "object"
}
```

### 5.53 `GraphEvidence`

```json
{
  "additional_properties": false,
  "fields": {
    "excerpt": {
      "nullable": false,
      "required": true,
      "schema": {
        "type": "string"
      }
    },
    "graph_revision": {
      "nullable": false,
      "required": true,
      "schema": {
        "pattern": "^[0-9a-f]{64}$",
        "type": "string"
      }
    },
    "relation_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "pattern": "^REL-[0-9a-f]{20}$",
        "type": "string"
      }
    },
    "source_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "title": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "type": {
      "nullable": false,
      "required": true,
      "schema": {
        "enum": [
          "GRAPH"
        ],
        "type": "string"
      }
    }
  },
  "type": "object"
}
```

### 5.54 `GraphNode`

```json
{
  "additional_properties": false,
  "fields": {
    "business_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "label": {
      "nullable": false,
      "required": true,
      "schema": {
        "enum": [
          "Area",
          "Chamber",
          "Equipment",
          "EquipmentModel",
          "Parameter",
          "ProcessStep"
        ],
        "type": "string"
      }
    },
    "name": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "node_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "properties": {
      "nullable": false,
      "required": true,
      "schema": {
        "additional_properties": true,
        "fields": {},
        "type": "object"
      }
    }
  },
  "type": "object"
}
```

### 5.55 `GraphQueryRequest`

```json
{
  "additional_properties": false,
  "fields": {
    "question": {
      "nullable": false,
      "required": true,
      "schema": {
        "max_length": 1000,
        "min_length": 1,
        "type": "string"
      }
    }
  },
  "type": "object"
}
```

### 5.56 `GraphQueryResponse`

```json
{
  "additional_properties": false,
  "fields": {
    "columns": {
      "nullable": false,
      "required": true,
      "schema": {
        "items": {
          "type": "string"
        },
        "type": "array"
      }
    },
    "error_msg": {
      "nullable": true,
      "required": false,
      "schema": {
        "type": "string"
      }
    },
    "generated_cypher": {
      "nullable": true,
      "required": false,
      "schema": {
        "type": "string"
      }
    },
    "is_rejected": {
      "nullable": false,
      "required": true,
      "schema": {
        "type": "boolean"
      }
    },
    "is_valid": {
      "nullable": false,
      "required": true,
      "schema": {
        "type": "boolean"
      }
    },
    "latency_ms": {
      "nullable": false,
      "required": true,
      "schema": {
        "minimum": 0.0,
        "type": "integer"
      }
    },
    "question": {
      "nullable": false,
      "required": true,
      "schema": {
        "type": "string"
      }
    },
    "reject_reason": {
      "nullable": true,
      "required": false,
      "schema": {
        "type": "string"
      }
    },
    "row_count": {
      "nullable": false,
      "required": true,
      "schema": {
        "minimum": 0.0,
        "type": "integer"
      }
    },
    "rows": {
      "nullable": false,
      "required": true,
      "schema": {
        "items": {
          "additional_properties": true,
          "fields": {},
          "type": "object"
        },
        "type": "array"
      }
    }
  },
  "type": "object"
}
```

### 5.57 `GraphRelationship`

```json
{
  "additional_properties": false,
  "fields": {
    "from_node_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "relation_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "pattern": "^REL-[0-9a-f]{20}$",
        "type": "string"
      }
    },
    "to_node_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "type": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    }
  },
  "type": "object"
}
```

### 5.58 `GroupedMetricResult`

```json
{
  "additional_properties": false,
  "fields": {
    "group": {
      "nullable": false,
      "required": true,
      "schema": {
        "additional_properties": true,
        "fields": {},
        "type": "object"
      }
    },
    "value": {
      "nullable": false,
      "required": false,
      "schema": {
        "type": "union",
        "variants": [
          {
            "type": "integer"
          },
          {
            "type": "number"
          },
          {
            "type": "null"
          }
        ]
      }
    }
  },
  "type": "object"
}
```

### 5.59 `HTTPValidationError`

```json
{
  "additional_properties": true,
  "fields": {
    "detail": {
      "nullable": false,
      "required": false,
      "schema": {
        "items": {
          "additional_properties": true,
          "fields": {
            "loc": {
              "nullable": false,
              "required": true,
              "schema": {
                "items": {
                  "type": "union",
                  "variants": [
                    {
                      "type": "string"
                    },
                    {
                      "type": "integer"
                    }
                  ]
                },
                "type": "array"
              }
            },
            "msg": {
              "nullable": false,
              "required": true,
              "schema": {
                "type": "string"
              }
            },
            "type": {
              "nullable": false,
              "required": true,
              "schema": {
                "type": "string"
              }
            }
          },
          "type": "object"
        },
        "type": "array"
      }
    }
  },
  "type": "object"
}
```

### 5.60 `HealthResponse`

```json
{
  "additional_properties": false,
  "fields": {
    "status": {
      "nullable": false,
      "required": true,
      "schema": {
        "enum": [
          "UP"
        ],
        "type": "string"
      }
    }
  },
  "type": "object"
}
```

### 5.61 `MetricPlan`

```json
{
  "additional_properties": false,
  "fields": {
    "column": {
      "nullable": true,
      "required": false,
      "schema": {
        "type": "string"
      }
    },
    "p": {
      "nullable": true,
      "required": false,
      "schema": {
        "maximum": 100.0,
        "minimum": 0.0,
        "type": "number"
      }
    },
    "type": {
      "nullable": false,
      "required": true,
      "schema": {
        "enum": [
          "count",
          "max",
          "mean",
          "median",
          "min",
          "percentile",
          "ratio",
          "std",
          "sum"
        ],
        "type": "string"
      }
    }
  },
  "type": "object"
}
```

### 5.62 `MetrologyAskEvidence`

```json
{
  "additional_properties": false,
  "fields": {
    "excerpt": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "source_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "title": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "type": {
      "nullable": false,
      "required": true,
      "schema": {
        "type": "string"
      }
    }
  },
  "type": "object"
}
```

### 5.63 `MetrologyEvidence`

```json
{
  "additional_properties": false,
  "fields": {
    "excerpt": {
      "nullable": false,
      "required": true,
      "schema": {
        "type": "string"
      }
    },
    "source_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "title": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "type": {
      "nullable": false,
      "required": true,
      "schema": {
        "enum": [
          "METROLOGY"
        ],
        "type": "string"
      }
    }
  },
  "rules": [
    "metrology.alarm_result is forbidden"
  ],
  "type": "object"
}
```

### 5.64 `NlQueryHistoryResponse`

```json
{
  "additional_properties": false,
  "fields": {
    "items": {
      "nullable": false,
      "required": true,
      "schema": {
        "items": {
          "additional_properties": false,
          "fields": {
            "asked_at": {
              "nullable": false,
              "required": true,
              "schema": {
                "format": "date-time",
                "type": "string"
              }
            },
            "error_msg": {
              "nullable": true,
              "required": false,
              "schema": {
                "type": "string"
              }
            },
            "generated_sql": {
              "nullable": true,
              "required": false,
              "schema": {
                "type": "string"
              }
            },
            "is_rejected": {
              "nullable": false,
              "required": true,
              "schema": {
                "type": "boolean"
              }
            },
            "is_valid": {
              "nullable": false,
              "required": true,
              "schema": {
                "type": "boolean"
              }
            },
            "latency_ms": {
              "nullable": true,
              "required": false,
              "schema": {
                "minimum": 0.0,
                "type": "integer"
              }
            },
            "nl_query_log_id": {
              "nullable": false,
              "required": true,
              "schema": {
                "minimum": 1.0,
                "type": "integer"
              }
            },
            "outcome": {
              "nullable": false,
              "required": true,
              "schema": {
                "enum": [
                  "DB_ERROR",
                  "POLICY_REJECTED",
                  "SUCCESS",
                  "VALIDATION_FAILED"
                ],
                "type": "string"
              }
            },
            "question": {
              "nullable": false,
              "required": true,
              "schema": {
                "type": "string"
              }
            },
            "reject_reason": {
              "nullable": true,
              "required": false,
              "schema": {
                "type": "string"
              }
            },
            "row_cnt": {
              "nullable": true,
              "required": false,
              "schema": {
                "minimum": 0.0,
                "type": "integer"
              }
            }
          },
          "type": "object"
        },
        "type": "array"
      }
    },
    "page": {
      "nullable": false,
      "required": true,
      "schema": {
        "minimum": 1.0,
        "type": "integer"
      }
    },
    "size": {
      "nullable": false,
      "required": true,
      "schema": {
        "maximum": 100.0,
        "minimum": 1.0,
        "type": "integer"
      }
    },
    "total": {
      "nullable": false,
      "required": true,
      "schema": {
        "minimum": 0.0,
        "type": "integer"
      }
    }
  },
  "type": "object"
}
```

### 5.65 `NlQueryLogItem`

```json
{
  "additional_properties": false,
  "fields": {
    "asked_at": {
      "nullable": false,
      "required": true,
      "schema": {
        "format": "date-time",
        "type": "string"
      }
    },
    "error_msg": {
      "nullable": true,
      "required": false,
      "schema": {
        "type": "string"
      }
    },
    "generated_sql": {
      "nullable": true,
      "required": false,
      "schema": {
        "type": "string"
      }
    },
    "is_rejected": {
      "nullable": false,
      "required": true,
      "schema": {
        "type": "boolean"
      }
    },
    "is_valid": {
      "nullable": false,
      "required": true,
      "schema": {
        "type": "boolean"
      }
    },
    "latency_ms": {
      "nullable": true,
      "required": false,
      "schema": {
        "minimum": 0.0,
        "type": "integer"
      }
    },
    "nl_query_log_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "minimum": 1.0,
        "type": "integer"
      }
    },
    "outcome": {
      "nullable": false,
      "required": true,
      "schema": {
        "enum": [
          "DB_ERROR",
          "POLICY_REJECTED",
          "SUCCESS",
          "VALIDATION_FAILED"
        ],
        "type": "string"
      }
    },
    "question": {
      "nullable": false,
      "required": true,
      "schema": {
        "type": "string"
      }
    },
    "reject_reason": {
      "nullable": true,
      "required": false,
      "schema": {
        "type": "string"
      }
    },
    "row_cnt": {
      "nullable": true,
      "required": false,
      "schema": {
        "minimum": 0.0,
        "type": "integer"
      }
    }
  },
  "type": "object"
}
```

### 5.66 `NlQueryOutcome`

```json
{
  "enum": [
    "DB_ERROR",
    "POLICY_REJECTED",
    "SUCCESS",
    "VALIDATION_FAILED"
  ],
  "type": "string"
}
```

### 5.67 `ParameterItem`

```json
{
  "additional_properties": false,
  "fields": {
    "LCL": {
      "nullable": true,
      "required": true,
      "schema": {
        "type": "number"
      }
    },
    "LSL": {
      "nullable": true,
      "required": true,
      "schema": {
        "type": "number"
      }
    },
    "TARGET": {
      "nullable": false,
      "required": true,
      "schema": {
        "type": "number"
      }
    },
    "UCL": {
      "nullable": false,
      "required": true,
      "schema": {
        "type": "number"
      }
    },
    "USL": {
      "nullable": false,
      "required": true,
      "schema": {
        "type": "number"
      }
    },
    "area": {
      "nullable": false,
      "required": true,
      "schema": {
        "enum": [
          "Etch",
          "Photo"
        ],
        "type": "string"
      }
    },
    "ctrl_lower": {
      "nullable": true,
      "required": true,
      "schema": {
        "type": "number"
      }
    },
    "ctrl_upper": {
      "nullable": false,
      "required": true,
      "schema": {
        "type": "number"
      }
    },
    "name": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "parameter_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "parameter_name": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "spec_lower": {
      "nullable": true,
      "required": true,
      "schema": {
        "type": "number"
      }
    },
    "spec_upper": {
      "nullable": false,
      "required": true,
      "schema": {
        "type": "number"
      }
    },
    "target_value": {
      "nullable": false,
      "required": true,
      "schema": {
        "type": "number"
      }
    },
    "unit": {
      "nullable": true,
      "required": true,
      "schema": {
        "type": "string"
      }
    },
    "upper_only": {
      "nullable": false,
      "required": true,
      "schema": {
        "type": "boolean"
      }
    }
  },
  "type": "object"
}
```

### 5.68 `PublicAgentRunItem`

```json
{
  "additional_properties": false,
  "fields": {
    "action_id": {
      "nullable": true,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "agent_run_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "alarm_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "alarm_source": {
      "nullable": false,
      "required": true,
      "schema": {
        "enum": [
          "R03",
          "SUMMARY",
          "TRACE"
        ],
        "type": "string"
      }
    },
    "approval_id": {
      "nullable": true,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "chamber": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "chamber_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "confidence": {
      "nullable": true,
      "required": true,
      "schema": {
        "maximum": 1.0,
        "minimum": 0.0,
        "type": "number"
      }
    },
    "created_at": {
      "nullable": false,
      "required": true,
      "schema": {
        "format": "date-time",
        "type": "string"
      }
    },
    "deliveries": {
      "nullable": false,
      "required": true,
      "schema": {
        "items": {
          "additional_properties": false,
          "fields": {
            "channel": {
              "nullable": false,
              "required": true,
              "schema": {
                "enum": [
                  "EMAIL",
                  "MES"
                ],
                "type": "string"
              }
            },
            "status": {
              "nullable": false,
              "required": true,
              "schema": {
                "enum": [
                  "BLOCKED",
                  "CANCELED",
                  "FAILED",
                  "SENDING",
                  "SENT",
                  "UNKNOWN",
                  "WAITING"
                ],
                "type": "string"
              }
            }
          },
          "type": "object"
        },
        "type": "array"
      }
    },
    "fault_code": {
      "nullable": true,
      "required": true,
      "schema": {
        "enum": [
          "FOC",
          "MFD",
          "OTH",
          "RFM",
          "TMD"
        ],
        "type": "string"
      }
    },
    "fault_color": {
      "nullable": false,
      "required": true,
      "schema": {
        "type": "null"
      }
    },
    "fault_name": {
      "nullable": false,
      "required": true,
      "schema": {
        "type": "null"
      }
    },
    "latency_ms": {
      "nullable": false,
      "required": true,
      "schema": {
        "minimum": 0.0,
        "type": "integer"
      }
    },
    "llm_model": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "predicted_fault_code": {
      "nullable": true,
      "required": true,
      "schema": {
        "enum": [
          "FOC",
          "MFD",
          "OTH",
          "RFM",
          "TMD"
        ],
        "type": "string"
      }
    },
    "recommended_action": {
      "nullable": true,
      "required": true,
      "schema": {
        "enum": [
          "EQP_HOLD",
          "MONITORING",
          "WARNING"
        ],
        "type": "string"
      }
    },
    "status": {
      "nullable": false,
      "required": true,
      "schema": {
        "enum": [
          "COMPLETED",
          "FAILED",
          "RUNNING",
          "WAITING_APPROVAL"
        ],
        "type": "string"
      }
    },
    "tools": {
      "nullable": false,
      "required": true,
      "schema": {
        "items": {
          "additional_properties": false,
          "fields": {
            "n": {
              "nullable": false,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            },
            "result_summary": {
              "nullable": false,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            },
            "s": {
              "nullable": false,
              "required": true,
              "schema": {
                "enum": [
                  "ERROR",
                  "SUCCESS",
                  "TIMEOUT"
                ],
                "type": "string"
              }
            },
            "status": {
              "nullable": false,
              "required": true,
              "schema": {
                "enum": [
                  "ERROR",
                  "SUCCESS",
                  "TIMEOUT"
                ],
                "type": "string"
              }
            },
            "tool_name": {
              "nullable": false,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            }
          },
          "type": "object"
        },
        "type": "array"
      }
    }
  },
  "type": "object"
}
```

### 5.69 `PublicApprovalDecision`

```json
{
  "enum": [
    "APPROVED",
    "REJECTED"
  ],
  "type": "string"
}
```

### 5.70 `PublicApprovalItem`

```json
{
  "additional_properties": false,
  "fields": {
    "action_code": {
      "nullable": false,
      "required": true,
      "schema": {
        "enum": [
          "EQP_HOLD",
          "MONITORING",
          "WARNING"
        ],
        "type": "string"
      }
    },
    "action_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "agent_run_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "approval_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "approved_at": {
      "nullable": true,
      "required": true,
      "schema": {
        "format": "date-time",
        "type": "string"
      }
    },
    "approved_by": {
      "nullable": true,
      "required": true,
      "schema": {
        "type": "string"
      }
    },
    "chamber": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "chamber_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "created_at": {
      "nullable": false,
      "required": true,
      "schema": {
        "format": "date-time",
        "type": "string"
      }
    },
    "decided_at": {
      "nullable": true,
      "required": true,
      "schema": {
        "format": "date-time",
        "type": "string"
      }
    },
    "decided_by": {
      "nullable": true,
      "required": true,
      "schema": {
        "type": "string"
      }
    },
    "decision_comment": {
      "nullable": true,
      "required": true,
      "schema": {
        "type": "string"
      }
    },
    "equipment": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "equipment_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "fault_code": {
      "nullable": false,
      "required": true,
      "schema": {
        "enum": [
          "FOC",
          "MFD",
          "OTH",
          "RFM",
          "TMD"
        ],
        "type": "string"
      }
    },
    "lot": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "lot_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "predicted_fault_code": {
      "nullable": false,
      "required": true,
      "schema": {
        "enum": [
          "FOC",
          "MFD",
          "OTH",
          "RFM",
          "TMD"
        ],
        "type": "string"
      }
    },
    "reason": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "status": {
      "nullable": false,
      "required": true,
      "schema": {
        "enum": [
          "APPROVED",
          "PENDING",
          "REJECTED"
        ],
        "type": "string"
      }
    }
  },
  "type": "object"
}
```

### 5.71 `PublicApprovalStatus`

```json
{
  "enum": [
    "APPROVED",
    "PENDING",
    "REJECTED"
  ],
  "type": "string"
}
```

### 5.72 `PublicDeliveryChannel`

```json
{
  "enum": [
    "EMAIL",
    "MES"
  ],
  "type": "string"
}
```

### 5.73 `PublicDeliveryItem`

```json
{
  "additional_properties": false,
  "fields": {
    "channel": {
      "nullable": false,
      "required": true,
      "schema": {
        "enum": [
          "EMAIL",
          "MES"
        ],
        "type": "string"
      }
    },
    "status": {
      "nullable": false,
      "required": true,
      "schema": {
        "enum": [
          "BLOCKED",
          "CANCELED",
          "FAILED",
          "SENDING",
          "SENT",
          "UNKNOWN",
          "WAITING"
        ],
        "type": "string"
      }
    }
  },
  "type": "object"
}
```

### 5.74 `PublicToolCallItem`

```json
{
  "additional_properties": false,
  "fields": {
    "n": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "result_summary": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "s": {
      "nullable": false,
      "required": true,
      "schema": {
        "enum": [
          "ERROR",
          "SUCCESS",
          "TIMEOUT"
        ],
        "type": "string"
      }
    },
    "status": {
      "nullable": false,
      "required": true,
      "schema": {
        "enum": [
          "ERROR",
          "SUCCESS",
          "TIMEOUT"
        ],
        "type": "string"
      }
    },
    "tool_name": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    }
  },
  "type": "object"
}
```

### 5.75 `ReadinessCheck`

```json
{
  "additional_properties": false,
  "fields": {
    "latency_ms": {
      "nullable": false,
      "required": true,
      "schema": {
        "minimum": 0,
        "type": "integer"
      }
    },
    "reason_code": {
      "nullable": true,
      "required": true,
      "schema": {
        "enum": [
          "CONTRACT_MISMATCH",
          "DEPENDENCY_UNAVAILABLE",
          "KAFKA_LAG_STALE",
          "NOT_CONFIGURED",
          "RAG_MODEL_NOT_READY",
          "TIMEOUT"
        ],
        "type": "string"
      }
    },
    "status": {
      "nullable": false,
      "required": true,
      "schema": {
        "enum": [
          "FAIL",
          "PASS"
        ],
        "type": "string"
      }
    }
  },
  "type": "object"
}
```

### 5.76 `ReadinessChecks`

```json
{
  "additional_properties": false,
  "fields": {
    "kafka": {
      "nullable": false,
      "required": true,
      "schema": {
        "additional_properties": false,
        "fields": {
          "latency_ms": {
            "nullable": false,
            "required": true,
            "schema": {
              "minimum": 0,
              "type": "integer"
            }
          },
          "reason_code": {
            "nullable": true,
            "required": true,
            "schema": {
              "enum": [
                "CONTRACT_MISMATCH",
                "DEPENDENCY_UNAVAILABLE",
                "KAFKA_LAG_STALE",
                "NOT_CONFIGURED",
                "RAG_MODEL_NOT_READY",
                "TIMEOUT"
              ],
              "type": "string"
            }
          },
          "status": {
            "nullable": false,
            "required": true,
            "schema": {
              "enum": [
                "FAIL",
                "PASS"
              ],
              "type": "string"
            }
          }
        },
        "type": "object"
      }
    },
    "n8n": {
      "nullable": false,
      "required": true,
      "schema": {
        "additional_properties": false,
        "fields": {
          "latency_ms": {
            "nullable": false,
            "required": true,
            "schema": {
              "minimum": 0,
              "type": "integer"
            }
          },
          "reason_code": {
            "nullable": true,
            "required": true,
            "schema": {
              "enum": [
                "CONTRACT_MISMATCH",
                "DEPENDENCY_UNAVAILABLE",
                "KAFKA_LAG_STALE",
                "NOT_CONFIGURED",
                "RAG_MODEL_NOT_READY",
                "TIMEOUT"
              ],
              "type": "string"
            }
          },
          "status": {
            "nullable": false,
            "required": true,
            "schema": {
              "enum": [
                "FAIL",
                "PASS"
              ],
              "type": "string"
            }
          }
        },
        "type": "object"
      }
    },
    "neo4j": {
      "nullable": false,
      "required": true,
      "schema": {
        "additional_properties": false,
        "fields": {
          "latency_ms": {
            "nullable": false,
            "required": true,
            "schema": {
              "minimum": 0,
              "type": "integer"
            }
          },
          "reason_code": {
            "nullable": true,
            "required": true,
            "schema": {
              "enum": [
                "CONTRACT_MISMATCH",
                "DEPENDENCY_UNAVAILABLE",
                "KAFKA_LAG_STALE",
                "NOT_CONFIGURED",
                "RAG_MODEL_NOT_READY",
                "TIMEOUT"
              ],
              "type": "string"
            }
          },
          "status": {
            "nullable": false,
            "required": true,
            "schema": {
              "enum": [
                "FAIL",
                "PASS"
              ],
              "type": "string"
            }
          }
        },
        "type": "object"
      }
    },
    "postgresql_runtime": {
      "nullable": false,
      "required": true,
      "schema": {
        "additional_properties": false,
        "fields": {
          "latency_ms": {
            "nullable": false,
            "required": true,
            "schema": {
              "minimum": 0,
              "type": "integer"
            }
          },
          "reason_code": {
            "nullable": true,
            "required": true,
            "schema": {
              "enum": [
                "CONTRACT_MISMATCH",
                "DEPENDENCY_UNAVAILABLE",
                "KAFKA_LAG_STALE",
                "NOT_CONFIGURED",
                "RAG_MODEL_NOT_READY",
                "TIMEOUT"
              ],
              "type": "string"
            }
          },
          "status": {
            "nullable": false,
            "required": true,
            "schema": {
              "enum": [
                "FAIL",
                "PASS"
              ],
              "type": "string"
            }
          }
        },
        "type": "object"
      }
    },
    "rag": {
      "nullable": false,
      "required": true,
      "schema": {
        "additional_properties": false,
        "fields": {
          "latency_ms": {
            "nullable": false,
            "required": true,
            "schema": {
              "minimum": 0,
              "type": "integer"
            }
          },
          "reason_code": {
            "nullable": true,
            "required": true,
            "schema": {
              "enum": [
                "CONTRACT_MISMATCH",
                "DEPENDENCY_UNAVAILABLE",
                "KAFKA_LAG_STALE",
                "NOT_CONFIGURED",
                "RAG_MODEL_NOT_READY",
                "TIMEOUT"
              ],
              "type": "string"
            }
          },
          "status": {
            "nullable": false,
            "required": true,
            "schema": {
              "enum": [
                "FAIL",
                "PASS"
              ],
              "type": "string"
            }
          }
        },
        "type": "object"
      }
    },
    "reference_migration": {
      "nullable": false,
      "required": true,
      "schema": {
        "additional_properties": false,
        "fields": {
          "latency_ms": {
            "nullable": false,
            "required": true,
            "schema": {
              "minimum": 0,
              "type": "integer"
            }
          },
          "reason_code": {
            "nullable": true,
            "required": true,
            "schema": {
              "enum": [
                "CONTRACT_MISMATCH",
                "DEPENDENCY_UNAVAILABLE",
                "KAFKA_LAG_STALE",
                "NOT_CONFIGURED",
                "RAG_MODEL_NOT_READY",
                "TIMEOUT"
              ],
              "type": "string"
            }
          },
          "status": {
            "nullable": false,
            "required": true,
            "schema": {
              "enum": [
                "FAIL",
                "PASS"
              ],
              "type": "string"
            }
          }
        },
        "type": "object"
      }
    }
  },
  "type": "object"
}
```

### 5.77 `ReadinessResponse`

```json
{
  "additional_properties": false,
  "fields": {
    "checks": {
      "nullable": false,
      "required": true,
      "schema": {
        "additional_properties": false,
        "fields": {
          "kafka": {
            "nullable": false,
            "required": true,
            "schema": {
              "additional_properties": false,
              "fields": {
                "latency_ms": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "minimum": 0,
                    "type": "integer"
                  }
                },
                "reason_code": {
                  "nullable": true,
                  "required": true,
                  "schema": {
                    "enum": [
                      "CONTRACT_MISMATCH",
                      "DEPENDENCY_UNAVAILABLE",
                      "KAFKA_LAG_STALE",
                      "NOT_CONFIGURED",
                      "RAG_MODEL_NOT_READY",
                      "TIMEOUT"
                    ],
                    "type": "string"
                  }
                },
                "status": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "enum": [
                      "FAIL",
                      "PASS"
                    ],
                    "type": "string"
                  }
                }
              },
              "type": "object"
            }
          },
          "n8n": {
            "nullable": false,
            "required": true,
            "schema": {
              "additional_properties": false,
              "fields": {
                "latency_ms": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "minimum": 0,
                    "type": "integer"
                  }
                },
                "reason_code": {
                  "nullable": true,
                  "required": true,
                  "schema": {
                    "enum": [
                      "CONTRACT_MISMATCH",
                      "DEPENDENCY_UNAVAILABLE",
                      "KAFKA_LAG_STALE",
                      "NOT_CONFIGURED",
                      "RAG_MODEL_NOT_READY",
                      "TIMEOUT"
                    ],
                    "type": "string"
                  }
                },
                "status": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "enum": [
                      "FAIL",
                      "PASS"
                    ],
                    "type": "string"
                  }
                }
              },
              "type": "object"
            }
          },
          "neo4j": {
            "nullable": false,
            "required": true,
            "schema": {
              "additional_properties": false,
              "fields": {
                "latency_ms": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "minimum": 0,
                    "type": "integer"
                  }
                },
                "reason_code": {
                  "nullable": true,
                  "required": true,
                  "schema": {
                    "enum": [
                      "CONTRACT_MISMATCH",
                      "DEPENDENCY_UNAVAILABLE",
                      "KAFKA_LAG_STALE",
                      "NOT_CONFIGURED",
                      "RAG_MODEL_NOT_READY",
                      "TIMEOUT"
                    ],
                    "type": "string"
                  }
                },
                "status": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "enum": [
                      "FAIL",
                      "PASS"
                    ],
                    "type": "string"
                  }
                }
              },
              "type": "object"
            }
          },
          "postgresql_runtime": {
            "nullable": false,
            "required": true,
            "schema": {
              "additional_properties": false,
              "fields": {
                "latency_ms": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "minimum": 0,
                    "type": "integer"
                  }
                },
                "reason_code": {
                  "nullable": true,
                  "required": true,
                  "schema": {
                    "enum": [
                      "CONTRACT_MISMATCH",
                      "DEPENDENCY_UNAVAILABLE",
                      "KAFKA_LAG_STALE",
                      "NOT_CONFIGURED",
                      "RAG_MODEL_NOT_READY",
                      "TIMEOUT"
                    ],
                    "type": "string"
                  }
                },
                "status": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "enum": [
                      "FAIL",
                      "PASS"
                    ],
                    "type": "string"
                  }
                }
              },
              "type": "object"
            }
          },
          "rag": {
            "nullable": false,
            "required": true,
            "schema": {
              "additional_properties": false,
              "fields": {
                "latency_ms": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "minimum": 0,
                    "type": "integer"
                  }
                },
                "reason_code": {
                  "nullable": true,
                  "required": true,
                  "schema": {
                    "enum": [
                      "CONTRACT_MISMATCH",
                      "DEPENDENCY_UNAVAILABLE",
                      "KAFKA_LAG_STALE",
                      "NOT_CONFIGURED",
                      "RAG_MODEL_NOT_READY",
                      "TIMEOUT"
                    ],
                    "type": "string"
                  }
                },
                "status": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "enum": [
                      "FAIL",
                      "PASS"
                    ],
                    "type": "string"
                  }
                }
              },
              "type": "object"
            }
          },
          "reference_migration": {
            "nullable": false,
            "required": true,
            "schema": {
              "additional_properties": false,
              "fields": {
                "latency_ms": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "minimum": 0,
                    "type": "integer"
                  }
                },
                "reason_code": {
                  "nullable": true,
                  "required": true,
                  "schema": {
                    "enum": [
                      "CONTRACT_MISMATCH",
                      "DEPENDENCY_UNAVAILABLE",
                      "KAFKA_LAG_STALE",
                      "NOT_CONFIGURED",
                      "RAG_MODEL_NOT_READY",
                      "TIMEOUT"
                    ],
                    "type": "string"
                  }
                },
                "status": {
                  "nullable": false,
                  "required": true,
                  "schema": {
                    "enum": [
                      "FAIL",
                      "PASS"
                    ],
                    "type": "string"
                  }
                }
              },
              "type": "object"
            }
          }
        },
        "type": "object"
      }
    },
    "dataset_epoch": {
      "nullable": false,
      "required": true,
      "schema": {
        "enum": [
          "fdc_final_20260818"
        ],
        "type": "string"
      }
    },
    "status": {
      "nullable": false,
      "required": true,
      "schema": {
        "enum": [
          "NOT_READY",
          "READY"
        ],
        "type": "string"
      }
    }
  },
  "rules": [
    "all checks PASS yields status READY and HTTP 200",
    "any check FAIL yields status NOT_READY and HTTP 503"
  ],
  "type": "object"
}
```

### 5.78 `RunAlarmEvidence`

```json
{
  "additional_properties": false,
  "fields": {
    "alarm": {
      "nullable": false,
      "required": true,
      "schema": {
        "additional_properties": false,
        "fields": {
          "alarm_id": {
            "nullable": false,
            "required": true,
            "schema": {
              "min_length": 1,
              "type": "string"
            }
          },
          "source": {
            "nullable": false,
            "required": true,
            "schema": {
              "enum": [
                "R03",
                "SUMMARY",
                "TRACE"
              ],
              "type": "string"
            }
          }
        },
        "type": "object"
      }
    },
    "excerpt": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "source_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "title": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "type": {
      "nullable": false,
      "required": true,
      "schema": {
        "type": "string"
      }
    }
  },
  "type": "object"
}
```

### 5.79 `RunStatus`

```json
{
  "enum": [
    "COMPLETED",
    "FAILED",
    "RUNNING",
    "WAITING_APPROVAL"
  ],
  "type": "string"
}
```

### 5.80 `SqlValidateRequest`

```json
{
  "additional_properties": false,
  "fields": {
    "sql": {
      "nullable": false,
      "required": true,
      "schema": {
        "max_length": 20000,
        "min_length": 1,
        "type": "string"
      }
    }
  },
  "type": "object"
}
```

### 5.81 `SqlValidateResponse`

```json
{
  "additional_properties": false,
  "fields": {
    "checks": {
      "nullable": true,
      "required": false,
      "schema": {
        "items": {
          "additional_properties": false,
          "fields": {
            "key": {
              "nullable": false,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            },
            "label": {
              "nullable": false,
              "required": true,
              "schema": {
                "min_length": 1,
                "type": "string"
              }
            },
            "ok": {
              "nullable": false,
              "required": true,
              "schema": {
                "type": "boolean"
              }
            }
          },
          "type": "object"
        },
        "type": "array"
      }
    },
    "normalized_sql": {
      "nullable": true,
      "required": false,
      "schema": {
        "type": "string"
      }
    },
    "reason": {
      "nullable": false,
      "required": true,
      "schema": {
        "type": "string"
      }
    },
    "valid": {
      "nullable": false,
      "required": true,
      "schema": {
        "type": "boolean"
      }
    }
  },
  "type": "object"
}
```

### 5.82 `ToolCallStatus`

```json
{
  "enum": [
    "ERROR",
    "SUCCESS",
    "TIMEOUT"
  ],
  "type": "string"
}
```

### 5.83 `TraceAskEvidence`

```json
{
  "additional_properties": false,
  "fields": {
    "excerpt": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "source_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "title": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "type": {
      "nullable": false,
      "required": true,
      "schema": {
        "type": "string"
      }
    }
  },
  "type": "object"
}
```

### 5.84 `TraceEvidence`

```json
{
  "additional_properties": false,
  "fields": {
    "excerpt": {
      "nullable": false,
      "required": true,
      "schema": {
        "type": "string"
      }
    },
    "source_id": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "title": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "type": {
      "nullable": false,
      "required": true,
      "schema": {
        "enum": [
          "TRACE"
        ],
        "type": "string"
      }
    }
  },
  "type": "object"
}
```

### 5.85 `TracePoint`

```json
{
  "additional_properties": false,
  "fields": {
    "measured_at": {
      "nullable": false,
      "required": true,
      "schema": {
        "format": "date-time",
        "type": "string"
      }
    },
    "recipe_step_no": {
      "nullable": false,
      "required": true,
      "schema": {
        "minimum": 1,
        "type": "integer"
      }
    },
    "seq_no": {
      "nullable": false,
      "required": true,
      "schema": {
        "minimum": 0,
        "type": "integer"
      }
    },
    "value": {
      "nullable": false,
      "required": true,
      "schema": {
        "type": "number"
      }
    }
  },
  "type": "object"
}
```

### 5.86 `ValidationCheck`

```json
{
  "additional_properties": false,
  "fields": {
    "key": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "label": {
      "nullable": false,
      "required": true,
      "schema": {
        "min_length": 1,
        "type": "string"
      }
    },
    "ok": {
      "nullable": false,
      "required": true,
      "schema": {
        "type": "boolean"
      }
    }
  },
  "type": "object"
}
```

### 5.87 `ValidationError`

```json
{
  "additional_properties": true,
  "fields": {
    "loc": {
      "nullable": false,
      "required": true,
      "schema": {
        "items": {
          "type": "union",
          "variants": [
            {
              "type": "string"
            },
            {
              "type": "integer"
            }
          ]
        },
        "type": "array"
      }
    },
    "msg": {
      "nullable": false,
      "required": true,
      "schema": {
        "type": "string"
      }
    },
    "type": {
      "nullable": false,
      "required": true,
      "schema": {
        "type": "string"
      }
    }
  },
  "type": "object"
}
```

### 5.88 `VisualizationPlan`

```json
{
  "additional_properties": false,
  "fields": {
    "chart_type": {
      "nullable": false,
      "required": true,
      "schema": {
        "enum": [
          "bar",
          "histogram",
          "line",
          "table"
        ],
        "type": "string"
      }
    },
    "x": {
      "nullable": true,
      "required": false,
      "schema": {
        "type": "string"
      }
    },
    "y": {
      "nullable": true,
      "required": false,
      "schema": {
        "type": "string"
      }
    }
  },
  "type": "object"
}
```

## 6. 감사 이벤트

| Event | Entity | 기록 주체 |
|---|---|---|
| `DETECTION_COMPLETED` | `LOT_HIST` | A |
| `AGENT_RUN_STARTED` | `AGENT_RUN` | C |
| `HYPOTHESIS_GENERATED` | `AGENT_RUN` | C |
| `APPROVAL_REQUESTED` | `APPROVAL` | C |
| `APPROVAL_DECIDED` | `APPROVAL` | C |
| `ACTION_SENT` | `ACTION` | C delivery service |
| `ACTION_SEND_FAILED` | `ACTION` | C delivery service |
| `AGENT_RUN_COMPLETED` | `AGENT_RUN` | C |
| `AGENT_RUN_FAILED` | `AGENT_RUN` | C |
