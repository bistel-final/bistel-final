// 트레이스 fixture — 명세 스키마(POST /traces/search · GET /traces/catalog)에 맞춘 mock
// TODO(api): 실서버 연결 시 이 파일만 교체하면 된다.
//
// 포인트 복원식: 스텝 3포인트 = [3*mean − min − max, min, max] (알람 detail 역산, §2 통계와 일치 검증)
// TODO(data): 포인트별 measured_at 실측 미확보 — occurred_at + 20초 간격 mock
// TODO(data): ET_REFL 한계선·단위 미확보 — null 이며 화면은 "한계선 미제공"으로 표기한다
//
// 한계선은 센서별로 다르다. 전역 상수를 두지 말고 반드시 catalog.sensors 또는
// search 응답의 limits[sensor_id]를 사용할 것 (ET_CF4에 PH_FOCUS 한계선을 쓰면 판정이 뒤집힌다).

export const TRACE_SENSORS = [
  {
    "sensor_id": "PH_FOCUS",
    "sensor_name": "Focus Offset",
    "unit": "nm",
    "spec_lower": -60,
    "ctrl_lower": -36,
    "target": 0,
    "ctrl_upper": 36,
    "spec_upper": 60
  },
  {
    "sensor_id": "PH_DOSE",
    "sensor_name": "Exposure Dose",
    "unit": "mJ/cm²",
    "spec_lower": 24,
    "ctrl_lower": 24.4,
    "target": 25,
    "ctrl_upper": 25.6,
    "spec_upper": 26
  },
  {
    "sensor_id": "ET_CF4",
    "sensor_name": "CF4 Flow",
    "unit": "sccm",
    "spec_lower": 74,
    "ctrl_lower": 76.4,
    "target": 80,
    "ctrl_upper": 83.6,
    "spec_upper": 86
  },
  {
    "sensor_id": "ET_REFL",
    "sensor_name": "Reflected Power",
    "unit": null,
    "spec_lower": null,
    "ctrl_lower": null,
    "target": null,
    "ctrl_upper": null,
    "spec_upper": null
  }
]

export const TRACE_CATALOG = {
  areas: [
  {
    "area_id": "PHOTO"
  },
  {
    "area_id": "ETCH"
  }
],
  equipments: [
  {
    "equipment_id": "PHO-01",
    "area_id": "PHOTO",
    "model_code": "PH-9000",
    "chambers": [
      "PHO-01-C1",
      "PHO-01-C2"
    ]
  },
  {
    "equipment_id": "ETC-01",
    "area_id": "ETCH",
    "model_code": "ET-7500",
    "chambers": [
      "ETC-01-C1",
      "ETC-01-C2"
    ]
  }
],
  sensors: TRACE_SENSORS,
  recipes: [
  {
    "recipe_id": "RCP-PH-A1",
    "area_id": "PHOTO"
  },
  {
    "recipe_id": "RCP-ET-B1",
    "area_id": "ETCH"
  }
],
  lots: [
  {
    "lot_id": "LOT-260003"
  },
  {
    "lot_id": "LOT-260004"
  },
  {
    "lot_id": "LOT-260005"
  },
  {
    "lot_id": "LOT-260006"
  },
  {
    "lot_id": "LOT-260007"
  },
  {
    "lot_id": "LOT-260008"
  },
  {
    "lot_id": "LOT-260009"
  },
  {
    "lot_id": "LOT-260010"
  }
],
}

// 참고 지표 — 판정에는 쓰지 않는다
export const TRACE_ANOMALY = { anomaly_score: 0.84, anomaly_threshold: 0.62 }

// 알람이 없어 포인트 복원이 불가능한 조합의 구간 통계 (지시서 §2 제공값)
export const MEASURED_STEP_STATS = {
  'LOT-260007|PHO-01-C1|PH_FOCUS|1,3,5': {
    DEVELOP: { mean: 16.612, std: 10.926, min: 0.144, max: 38.309 },
  },
}

export const WAFER_TRACES = [
  {
    "lot_hist_id": "LH-00052",
    "lot_id": "LOT-260003",
    "wafer_no": 2,
    "chamber_id": "ETC-01-C2",
    "equipment_id": "ETC-01",
    "sensor_id": "ET_REFL",
    "recipe_id": "RCP-ET-B1",
    "occurred_at": "2026-06-01 23:17:23",
    "points": [],
    "missing_steps": [
      "MAIN_ETCH"
    ]
  },
  {
    "lot_hist_id": "LH-00054",
    "lot_id": "LOT-260003",
    "wafer_no": 4,
    "chamber_id": "ETC-01-C2",
    "equipment_id": "ETC-01",
    "sensor_id": "ET_REFL",
    "recipe_id": "RCP-ET-B1",
    "occurred_at": "2026-06-01 23:22:23",
    "points": [],
    "missing_steps": [
      "MAIN_ETCH"
    ]
  },
  {
    "lot_hist_id": "LH-00058",
    "lot_id": "LOT-260003",
    "wafer_no": 8,
    "chamber_id": "ETC-01-C2",
    "equipment_id": "ETC-01",
    "sensor_id": "ET_REFL",
    "recipe_id": "RCP-ET-B1",
    "occurred_at": "2026-06-01 23:32:09",
    "points": [],
    "missing_steps": [
      "MAIN_ETCH"
    ]
  },
  {
    "lot_hist_id": "LH-00060",
    "lot_id": "LOT-260003",
    "wafer_no": 10,
    "chamber_id": "ETC-01-C2",
    "equipment_id": "ETC-01",
    "sensor_id": "ET_REFL",
    "recipe_id": "RCP-ET-B1",
    "occurred_at": "2026-06-01 23:36:49",
    "points": [],
    "missing_steps": [
      "MAIN_ETCH"
    ]
  },
  {
    "lot_hist_id": "LH-00072",
    "lot_id": "LOT-260004",
    "wafer_no": 2,
    "chamber_id": "ETC-01-C2",
    "equipment_id": "ETC-01",
    "sensor_id": "ET_REFL",
    "recipe_id": "RCP-ET-B1",
    "occurred_at": "2026-06-02 07:20:23",
    "points": [
      {
        "seq_no": 1,
        "recipe_step_no": 1,
        "recipe_step_name": "MAIN_ETCH",
        "measured_at": "2026-06-02 07:20:23",
        "value": 32.685
      },
      {
        "seq_no": 2,
        "recipe_step_no": 1,
        "recipe_step_name": "MAIN_ETCH",
        "measured_at": "2026-06-02 07:20:43",
        "value": 26.7
      },
      {
        "seq_no": 3,
        "recipe_step_no": 1,
        "recipe_step_name": "MAIN_ETCH",
        "measured_at": "2026-06-02 07:21:03",
        "value": 34.203
      }
    ]
  },
  {
    "lot_hist_id": "LH-00074",
    "lot_id": "LOT-260004",
    "wafer_no": 4,
    "chamber_id": "ETC-01-C2",
    "equipment_id": "ETC-01",
    "sensor_id": "ET_REFL",
    "recipe_id": "RCP-ET-B1",
    "occurred_at": "2026-06-02 07:25:36",
    "points": [
      {
        "seq_no": 1,
        "recipe_step_no": 1,
        "recipe_step_name": "MAIN_ETCH",
        "measured_at": "2026-06-02 07:25:36",
        "value": 30.784
      },
      {
        "seq_no": 2,
        "recipe_step_no": 1,
        "recipe_step_name": "MAIN_ETCH",
        "measured_at": "2026-06-02 07:25:56",
        "value": 26.602
      },
      {
        "seq_no": 3,
        "recipe_step_no": 1,
        "recipe_step_name": "MAIN_ETCH",
        "measured_at": "2026-06-02 07:26:16",
        "value": 38.128
      }
    ]
  },
  {
    "lot_hist_id": "LH-00076",
    "lot_id": "LOT-260004",
    "wafer_no": 6,
    "chamber_id": "ETC-01-C2",
    "equipment_id": "ETC-01",
    "sensor_id": "ET_REFL",
    "recipe_id": "RCP-ET-B1",
    "occurred_at": "2026-06-02 07:30:49",
    "points": [
      {
        "seq_no": 1,
        "recipe_step_no": 1,
        "recipe_step_name": "MAIN_ETCH",
        "measured_at": "2026-06-02 07:30:49",
        "value": 27.956
      },
      {
        "seq_no": 2,
        "recipe_step_no": 1,
        "recipe_step_name": "MAIN_ETCH",
        "measured_at": "2026-06-02 07:31:09",
        "value": 26.495
      },
      {
        "seq_no": 3,
        "recipe_step_no": 1,
        "recipe_step_name": "MAIN_ETCH",
        "measured_at": "2026-06-02 07:31:29",
        "value": 31.67
      }
    ]
  },
  {
    "lot_hist_id": "LH-00078",
    "lot_id": "LOT-260004",
    "wafer_no": 8,
    "chamber_id": "ETC-01-C2",
    "equipment_id": "ETC-01",
    "sensor_id": "ET_REFL",
    "recipe_id": "RCP-ET-B1",
    "occurred_at": "2026-06-02 07:35:44",
    "points": [
      {
        "seq_no": 1,
        "recipe_step_no": 1,
        "recipe_step_name": "MAIN_ETCH",
        "measured_at": "2026-06-02 07:35:44",
        "value": 28.164
      },
      {
        "seq_no": 2,
        "recipe_step_no": 1,
        "recipe_step_name": "MAIN_ETCH",
        "measured_at": "2026-06-02 07:36:04",
        "value": 23.074
      },
      {
        "seq_no": 3,
        "recipe_step_no": 1,
        "recipe_step_name": "MAIN_ETCH",
        "measured_at": "2026-06-02 07:36:24",
        "value": 33.119
      }
    ]
  },
  {
    "lot_hist_id": "LH-00080",
    "lot_id": "LOT-260004",
    "wafer_no": 10,
    "chamber_id": "ETC-01-C2",
    "equipment_id": "ETC-01",
    "sensor_id": "ET_REFL",
    "recipe_id": "RCP-ET-B1",
    "occurred_at": "2026-06-02 07:40:26",
    "points": [
      {
        "seq_no": 1,
        "recipe_step_no": 1,
        "recipe_step_name": "MAIN_ETCH",
        "measured_at": "2026-06-02 07:40:26",
        "value": 28.588
      },
      {
        "seq_no": 2,
        "recipe_step_no": 1,
        "recipe_step_name": "MAIN_ETCH",
        "measured_at": "2026-06-02 07:40:46",
        "value": 22.289
      },
      {
        "seq_no": 3,
        "recipe_step_no": 1,
        "recipe_step_name": "MAIN_ETCH",
        "measured_at": "2026-06-02 07:41:06",
        "value": 34.047
      }
    ]
  },
  {
    "lot_hist_id": "LH-00092",
    "lot_id": "LOT-260005",
    "wafer_no": 2,
    "chamber_id": "ETC-01-C2",
    "equipment_id": "ETC-01",
    "sensor_id": "ET_REFL",
    "recipe_id": "RCP-ET-B1",
    "occurred_at": "2026-06-02 15:19:49",
    "points": [
      {
        "seq_no": 1,
        "recipe_step_no": 1,
        "recipe_step_name": "MAIN_ETCH",
        "measured_at": "2026-06-02 15:19:49",
        "value": 42.012
      },
      {
        "seq_no": 2,
        "recipe_step_no": 1,
        "recipe_step_name": "MAIN_ETCH",
        "measured_at": "2026-06-02 15:20:09",
        "value": 26.919
      },
      {
        "seq_no": 3,
        "recipe_step_no": 1,
        "recipe_step_name": "MAIN_ETCH",
        "measured_at": "2026-06-02 15:20:29",
        "value": 43.386
      }
    ]
  },
  {
    "lot_hist_id": "LH-00094",
    "lot_id": "LOT-260005",
    "wafer_no": 4,
    "chamber_id": "ETC-01-C2",
    "equipment_id": "ETC-01",
    "sensor_id": "ET_REFL",
    "recipe_id": "RCP-ET-B1",
    "occurred_at": "2026-06-02 15:24:38",
    "points": [
      {
        "seq_no": 1,
        "recipe_step_no": 1,
        "recipe_step_name": "MAIN_ETCH",
        "measured_at": "2026-06-02 15:24:38",
        "value": 40.425
      },
      {
        "seq_no": 2,
        "recipe_step_no": 1,
        "recipe_step_name": "MAIN_ETCH",
        "measured_at": "2026-06-02 15:24:58",
        "value": 36.347
      },
      {
        "seq_no": 3,
        "recipe_step_no": 1,
        "recipe_step_name": "MAIN_ETCH",
        "measured_at": "2026-06-02 15:25:18",
        "value": 45.832
      }
    ]
  },
  {
    "lot_hist_id": "LH-00096",
    "lot_id": "LOT-260005",
    "wafer_no": 6,
    "chamber_id": "ETC-01-C2",
    "equipment_id": "ETC-01",
    "sensor_id": "ET_REFL",
    "recipe_id": "RCP-ET-B1",
    "occurred_at": "2026-06-02 15:29:45",
    "points": [
      {
        "seq_no": 1,
        "recipe_step_no": 1,
        "recipe_step_name": "MAIN_ETCH",
        "measured_at": "2026-06-02 15:29:45",
        "value": 40.192
      },
      {
        "seq_no": 2,
        "recipe_step_no": 1,
        "recipe_step_name": "MAIN_ETCH",
        "measured_at": "2026-06-02 15:30:05",
        "value": 34.439
      },
      {
        "seq_no": 3,
        "recipe_step_no": 1,
        "recipe_step_name": "MAIN_ETCH",
        "measured_at": "2026-06-02 15:30:25",
        "value": 43.617
      }
    ]
  },
  {
    "lot_hist_id": "LH-00098",
    "lot_id": "LOT-260005",
    "wafer_no": 8,
    "chamber_id": "ETC-01-C2",
    "equipment_id": "ETC-01",
    "sensor_id": "ET_REFL",
    "recipe_id": "RCP-ET-B1",
    "occurred_at": "2026-06-02 15:34:43",
    "points": [
      {
        "seq_no": 1,
        "recipe_step_no": 1,
        "recipe_step_name": "MAIN_ETCH",
        "measured_at": "2026-06-02 15:34:43",
        "value": 39.581
      },
      {
        "seq_no": 2,
        "recipe_step_no": 1,
        "recipe_step_name": "MAIN_ETCH",
        "measured_at": "2026-06-02 15:35:03",
        "value": 36.551
      },
      {
        "seq_no": 3,
        "recipe_step_no": 1,
        "recipe_step_name": "MAIN_ETCH",
        "measured_at": "2026-06-02 15:35:23",
        "value": 43.982
      }
    ]
  },
  {
    "lot_hist_id": "LH-00100",
    "lot_id": "LOT-260005",
    "wafer_no": 10,
    "chamber_id": "ETC-01-C2",
    "equipment_id": "ETC-01",
    "sensor_id": "ET_REFL",
    "recipe_id": "RCP-ET-B1",
    "occurred_at": "2026-06-02 15:39:42",
    "points": [
      {
        "seq_no": 1,
        "recipe_step_no": 1,
        "recipe_step_name": "MAIN_ETCH",
        "measured_at": "2026-06-02 15:39:42",
        "value": 35.135
      },
      {
        "seq_no": 2,
        "recipe_step_no": 1,
        "recipe_step_name": "MAIN_ETCH",
        "measured_at": "2026-06-02 15:40:02",
        "value": 29.984
      },
      {
        "seq_no": 3,
        "recipe_step_no": 1,
        "recipe_step_name": "MAIN_ETCH",
        "measured_at": "2026-06-02 15:40:22",
        "value": 44.756
      }
    ]
  },
  {
    "lot_hist_id": "LH-00101",
    "lot_id": "LOT-260006",
    "wafer_no": 1,
    "chamber_id": "PHO-01-C1",
    "equipment_id": "PHO-01",
    "sensor_id": "PH_FOCUS",
    "recipe_id": "RCP-PH-A1",
    "occurred_at": "2026-06-02 22:38:32",
    "points": [],
    "missing_steps": [
      "EXPOSE"
    ]
  },
  {
    "lot_hist_id": "LH-00103",
    "lot_id": "LOT-260006",
    "wafer_no": 3,
    "chamber_id": "PHO-01-C1",
    "equipment_id": "PHO-01",
    "sensor_id": "PH_FOCUS",
    "recipe_id": "RCP-PH-A1",
    "occurred_at": "2026-06-02 22:42:30",
    "points": [],
    "missing_steps": [
      "EXPOSE"
    ]
  },
  {
    "lot_hist_id": "LH-00109",
    "lot_id": "LOT-260006",
    "wafer_no": 9,
    "chamber_id": "PHO-01-C1",
    "equipment_id": "PHO-01",
    "sensor_id": "PH_FOCUS",
    "recipe_id": "RCP-PH-A1",
    "occurred_at": "2026-06-02 22:54:15",
    "points": [],
    "missing_steps": [
      "EXPOSE"
    ]
  },
  {
    "lot_hist_id": "LH-00121",
    "lot_id": "LOT-260007",
    "wafer_no": 1,
    "chamber_id": "PHO-01-C1",
    "equipment_id": "PHO-01",
    "sensor_id": "PH_FOCUS",
    "recipe_id": "RCP-PH-A1",
    "occurred_at": "2026-06-03 06:37:47",
    "points": [
      {
        "seq_no": 1,
        "recipe_step_no": 1,
        "recipe_step_name": "EXPOSE",
        "measured_at": "2026-06-03 06:37:47",
        "value": 57.315
      },
      {
        "seq_no": 2,
        "recipe_step_no": 1,
        "recipe_step_name": "EXPOSE",
        "measured_at": "2026-06-03 06:38:07",
        "value": 47.995
      },
      {
        "seq_no": 3,
        "recipe_step_no": 1,
        "recipe_step_name": "EXPOSE",
        "measured_at": "2026-06-03 06:38:27",
        "value": 67.226
      }
    ]
  },
  {
    "lot_hist_id": "LH-00123",
    "lot_id": "LOT-260007",
    "wafer_no": 3,
    "chamber_id": "PHO-01-C1",
    "equipment_id": "PHO-01",
    "sensor_id": "PH_FOCUS",
    "recipe_id": "RCP-PH-A1",
    "occurred_at": "2026-06-03 06:41:45",
    "points": [
      {
        "seq_no": 1,
        "recipe_step_no": 1,
        "recipe_step_name": "EXPOSE",
        "measured_at": "2026-06-03 06:41:45",
        "value": 56.335
      },
      {
        "seq_no": 2,
        "recipe_step_no": 1,
        "recipe_step_name": "EXPOSE",
        "measured_at": "2026-06-03 06:42:05",
        "value": 44.616
      },
      {
        "seq_no": 3,
        "recipe_step_no": 1,
        "recipe_step_name": "EXPOSE",
        "measured_at": "2026-06-03 06:42:25",
        "value": 69.377
      }
    ]
  },
  {
    "lot_hist_id": "LH-00125",
    "lot_id": "LOT-260007",
    "wafer_no": 5,
    "chamber_id": "PHO-01-C1",
    "equipment_id": "PHO-01",
    "sensor_id": "PH_FOCUS",
    "recipe_id": "RCP-PH-A1",
    "occurred_at": "2026-06-03 06:45:45",
    "points": [
      {
        "seq_no": 1,
        "recipe_step_no": 1,
        "recipe_step_name": "EXPOSE",
        "measured_at": "2026-06-03 06:45:45",
        "value": 54.753
      },
      {
        "seq_no": 2,
        "recipe_step_no": 1,
        "recipe_step_name": "EXPOSE",
        "measured_at": "2026-06-03 06:46:05",
        "value": 52.193
      },
      {
        "seq_no": 3,
        "recipe_step_no": 1,
        "recipe_step_name": "EXPOSE",
        "measured_at": "2026-06-03 06:46:25",
        "value": 65.353
      },
      {
        "seq_no": 4,
        "recipe_step_no": 2,
        "recipe_step_name": "DEVELOP",
        "measured_at": "2026-06-03 06:46:45",
        "value": 11.934
      },
      {
        "seq_no": 5,
        "recipe_step_no": 2,
        "recipe_step_name": "DEVELOP",
        "measured_at": "2026-06-03 06:47:05",
        "value": 6.294
      },
      {
        "seq_no": 6,
        "recipe_step_no": 2,
        "recipe_step_name": "DEVELOP",
        "measured_at": "2026-06-03 06:47:25",
        "value": 13.275
      }
    ]
  },
  {
    "lot_hist_id": "LH-00127",
    "lot_id": "LOT-260007",
    "wafer_no": 7,
    "chamber_id": "PHO-01-C1",
    "equipment_id": "PHO-01",
    "sensor_id": "PH_FOCUS",
    "recipe_id": "RCP-PH-A1",
    "occurred_at": "2026-06-03 06:49:46",
    "points": [
      {
        "seq_no": 1,
        "recipe_step_no": 1,
        "recipe_step_name": "EXPOSE",
        "measured_at": "2026-06-03 06:49:46",
        "value": 73.688
      },
      {
        "seq_no": 2,
        "recipe_step_no": 1,
        "recipe_step_name": "EXPOSE",
        "measured_at": "2026-06-03 06:50:06",
        "value": 56.816
      },
      {
        "seq_no": 3,
        "recipe_step_no": 1,
        "recipe_step_name": "EXPOSE",
        "measured_at": "2026-06-03 06:50:26",
        "value": 79.859
      }
    ]
  },
  {
    "lot_hist_id": "LH-00129",
    "lot_id": "LOT-260007",
    "wafer_no": 9,
    "chamber_id": "PHO-01-C1",
    "equipment_id": "PHO-01",
    "sensor_id": "PH_FOCUS",
    "recipe_id": "RCP-PH-A1",
    "occurred_at": "2026-06-03 06:53:46",
    "points": [
      {
        "seq_no": 1,
        "recipe_step_no": 1,
        "recipe_step_name": "EXPOSE",
        "measured_at": "2026-06-03 06:53:46",
        "value": 59.394
      },
      {
        "seq_no": 2,
        "recipe_step_no": 1,
        "recipe_step_name": "EXPOSE",
        "measured_at": "2026-06-03 06:54:06",
        "value": 29.038
      },
      {
        "seq_no": 3,
        "recipe_step_no": 1,
        "recipe_step_name": "EXPOSE",
        "measured_at": "2026-06-03 06:54:26",
        "value": 65.924
      }
    ]
  },
  {
    "lot_hist_id": "LH-00153",
    "lot_id": "LOT-260008",
    "wafer_no": 3,
    "chamber_id": "ETC-01-C1",
    "equipment_id": "ETC-01",
    "sensor_id": "ET_CF4",
    "recipe_id": "RCP-ET-B1",
    "occurred_at": "2026-06-03 15:43:05",
    "points": [],
    "missing_steps": [
      "MAIN_ETCH"
    ]
  },
  {
    "lot_hist_id": "LH-00155",
    "lot_id": "LOT-260008",
    "wafer_no": 5,
    "chamber_id": "ETC-01-C1",
    "equipment_id": "ETC-01",
    "sensor_id": "ET_CF4",
    "recipe_id": "RCP-ET-B1",
    "occurred_at": "2026-06-03 15:48:09",
    "points": [],
    "missing_steps": [
      "MAIN_ETCH"
    ]
  },
  {
    "lot_hist_id": "LH-00157",
    "lot_id": "LOT-260008",
    "wafer_no": 7,
    "chamber_id": "ETC-01-C1",
    "equipment_id": "ETC-01",
    "sensor_id": "ET_CF4",
    "recipe_id": "RCP-ET-B1",
    "occurred_at": "2026-06-03 15:52:49",
    "points": [],
    "missing_steps": [
      "MAIN_ETCH"
    ]
  },
  {
    "lot_hist_id": "LH-00141",
    "lot_id": "LOT-260008",
    "wafer_no": 1,
    "chamber_id": "PHO-01-C1",
    "equipment_id": "PHO-01",
    "sensor_id": "PH_FOCUS",
    "recipe_id": "RCP-PH-A1",
    "occurred_at": "2026-06-03 14:39:36",
    "points": [
      {
        "seq_no": 1,
        "recipe_step_no": 1,
        "recipe_step_name": "EXPOSE",
        "measured_at": "2026-06-03 14:39:36",
        "value": 58.116
      },
      {
        "seq_no": 2,
        "recipe_step_no": 1,
        "recipe_step_name": "EXPOSE",
        "measured_at": "2026-06-03 14:39:56",
        "value": 50.323
      },
      {
        "seq_no": 3,
        "recipe_step_no": 1,
        "recipe_step_name": "EXPOSE",
        "measured_at": "2026-06-03 14:40:16",
        "value": 64.16
      }
    ],
    "missing_steps": [
      "DEVELOP"
    ]
  },
  {
    "lot_hist_id": "LH-00143",
    "lot_id": "LOT-260008",
    "wafer_no": 3,
    "chamber_id": "PHO-01-C1",
    "equipment_id": "PHO-01",
    "sensor_id": "PH_FOCUS",
    "recipe_id": "RCP-PH-A1",
    "occurred_at": "2026-06-03 14:43:36",
    "points": [
      {
        "seq_no": 1,
        "recipe_step_no": 1,
        "recipe_step_name": "EXPOSE",
        "measured_at": "2026-06-03 14:43:36",
        "value": 87.318
      },
      {
        "seq_no": 2,
        "recipe_step_no": 1,
        "recipe_step_name": "EXPOSE",
        "measured_at": "2026-06-03 14:43:56",
        "value": 68.756
      },
      {
        "seq_no": 3,
        "recipe_step_no": 1,
        "recipe_step_name": "EXPOSE",
        "measured_at": "2026-06-03 14:44:16",
        "value": 98.029
      }
    ]
  },
  {
    "lot_hist_id": "LH-00145",
    "lot_id": "LOT-260008",
    "wafer_no": 5,
    "chamber_id": "PHO-01-C1",
    "equipment_id": "PHO-01",
    "sensor_id": "PH_FOCUS",
    "recipe_id": "RCP-PH-A1",
    "occurred_at": "2026-06-03 14:47:13",
    "points": [
      {
        "seq_no": 1,
        "recipe_step_no": 1,
        "recipe_step_name": "EXPOSE",
        "measured_at": "2026-06-03 14:47:13",
        "value": 69.123
      },
      {
        "seq_no": 2,
        "recipe_step_no": 1,
        "recipe_step_name": "EXPOSE",
        "measured_at": "2026-06-03 14:47:33",
        "value": 49.917
      },
      {
        "seq_no": 3,
        "recipe_step_no": 1,
        "recipe_step_name": "EXPOSE",
        "measured_at": "2026-06-03 14:47:53",
        "value": 78.465
      }
    ]
  },
  {
    "lot_hist_id": "LH-00147",
    "lot_id": "LOT-260008",
    "wafer_no": 7,
    "chamber_id": "PHO-01-C1",
    "equipment_id": "PHO-01",
    "sensor_id": "PH_FOCUS",
    "recipe_id": "RCP-PH-A1",
    "occurred_at": "2026-06-03 14:51:08",
    "points": [
      {
        "seq_no": 1,
        "recipe_step_no": 1,
        "recipe_step_name": "EXPOSE",
        "measured_at": "2026-06-03 14:51:08",
        "value": 63.262
      },
      {
        "seq_no": 2,
        "recipe_step_no": 1,
        "recipe_step_name": "EXPOSE",
        "measured_at": "2026-06-03 14:51:28",
        "value": 56.276
      },
      {
        "seq_no": 3,
        "recipe_step_no": 1,
        "recipe_step_name": "EXPOSE",
        "measured_at": "2026-06-03 14:51:48",
        "value": 91.671
      }
    ]
  },
  {
    "lot_hist_id": "LH-00149",
    "lot_id": "LOT-260008",
    "wafer_no": 9,
    "chamber_id": "PHO-01-C1",
    "equipment_id": "PHO-01",
    "sensor_id": "PH_FOCUS",
    "recipe_id": "RCP-PH-A1",
    "occurred_at": "2026-06-03 14:54:42",
    "points": [
      {
        "seq_no": 1,
        "recipe_step_no": 1,
        "recipe_step_name": "EXPOSE",
        "measured_at": "2026-06-03 14:54:42",
        "value": 73.964
      },
      {
        "seq_no": 2,
        "recipe_step_no": 1,
        "recipe_step_name": "EXPOSE",
        "measured_at": "2026-06-03 14:55:02",
        "value": 54.221
      },
      {
        "seq_no": 3,
        "recipe_step_no": 1,
        "recipe_step_name": "EXPOSE",
        "measured_at": "2026-06-03 14:55:22",
        "value": 79.835
      }
    ]
  },
  {
    "lot_hist_id": "LH-00171",
    "lot_id": "LOT-260009",
    "wafer_no": 1,
    "chamber_id": "ETC-01-C1",
    "equipment_id": "ETC-01",
    "sensor_id": "ET_CF4",
    "recipe_id": "RCP-ET-B1",
    "occurred_at": "2026-06-03 23:12:41",
    "points": [
      {
        "seq_no": 1,
        "recipe_step_no": 1,
        "recipe_step_name": "MAIN_ETCH",
        "measured_at": "2026-06-03 23:12:41",
        "value": 75.443
      },
      {
        "seq_no": 2,
        "recipe_step_no": 1,
        "recipe_step_name": "MAIN_ETCH",
        "measured_at": "2026-06-03 23:13:01",
        "value": 73.667
      },
      {
        "seq_no": 3,
        "recipe_step_no": 1,
        "recipe_step_name": "MAIN_ETCH",
        "measured_at": "2026-06-03 23:13:21",
        "value": 76.796
      }
    ]
  },
  {
    "lot_hist_id": "LH-00173",
    "lot_id": "LOT-260009",
    "wafer_no": 3,
    "chamber_id": "ETC-01-C1",
    "equipment_id": "ETC-01",
    "sensor_id": "ET_CF4",
    "recipe_id": "RCP-ET-B1",
    "occurred_at": "2026-06-03 23:17:24",
    "points": [
      {
        "seq_no": 1,
        "recipe_step_no": 1,
        "recipe_step_name": "MAIN_ETCH",
        "measured_at": "2026-06-03 23:17:24",
        "value": 73.369
      },
      {
        "seq_no": 2,
        "recipe_step_no": 1,
        "recipe_step_name": "MAIN_ETCH",
        "measured_at": "2026-06-03 23:17:44",
        "value": 73.586
      },
      {
        "seq_no": 3,
        "recipe_step_no": 1,
        "recipe_step_name": "MAIN_ETCH",
        "measured_at": "2026-06-03 23:18:04",
        "value": 75.243
      }
    ]
  },
  {
    "lot_hist_id": "LH-00175",
    "lot_id": "LOT-260009",
    "wafer_no": 5,
    "chamber_id": "ETC-01-C1",
    "equipment_id": "ETC-01",
    "sensor_id": "ET_CF4",
    "recipe_id": "RCP-ET-B1",
    "occurred_at": "2026-06-03 23:22:20",
    "points": [],
    "missing_steps": [
      "MAIN_ETCH"
    ]
  },
  {
    "lot_hist_id": "LH-00177",
    "lot_id": "LOT-260009",
    "wafer_no": 7,
    "chamber_id": "ETC-01-C1",
    "equipment_id": "ETC-01",
    "sensor_id": "ET_CF4",
    "recipe_id": "RCP-ET-B1",
    "occurred_at": "2026-06-03 23:27:11",
    "points": [],
    "missing_steps": [
      "MAIN_ETCH"
    ]
  },
  {
    "lot_hist_id": "LH-00179",
    "lot_id": "LOT-260009",
    "wafer_no": 9,
    "chamber_id": "ETC-01-C1",
    "equipment_id": "ETC-01",
    "sensor_id": "ET_CF4",
    "recipe_id": "RCP-ET-B1",
    "occurred_at": "2026-06-03 23:31:50",
    "points": [
      {
        "seq_no": 1,
        "recipe_step_no": 1,
        "recipe_step_name": "MAIN_ETCH",
        "measured_at": "2026-06-03 23:31:50",
        "value": 74.352
      },
      {
        "seq_no": 2,
        "recipe_step_no": 1,
        "recipe_step_name": "MAIN_ETCH",
        "measured_at": "2026-06-03 23:32:10",
        "value": 73.438
      },
      {
        "seq_no": 3,
        "recipe_step_no": 1,
        "recipe_step_name": "MAIN_ETCH",
        "measured_at": "2026-06-03 23:32:30",
        "value": 74.723
      }
    ]
  },
  {
    "lot_hist_id": "LH-00161",
    "lot_id": "LOT-260009",
    "wafer_no": 1,
    "chamber_id": "PHO-01-C1",
    "equipment_id": "PHO-01",
    "sensor_id": "PH_FOCUS",
    "recipe_id": "RCP-PH-A1",
    "occurred_at": "2026-06-03 22:26:40",
    "points": [
      {
        "seq_no": 1,
        "recipe_step_no": 1,
        "recipe_step_name": "EXPOSE",
        "measured_at": "2026-06-03 22:26:40",
        "value": 99.816
      },
      {
        "seq_no": 2,
        "recipe_step_no": 1,
        "recipe_step_name": "EXPOSE",
        "measured_at": "2026-06-03 22:27:00",
        "value": 67.438
      },
      {
        "seq_no": 3,
        "recipe_step_no": 1,
        "recipe_step_name": "EXPOSE",
        "measured_at": "2026-06-03 22:27:20",
        "value": 114.935
      }
    ],
    "missing_steps": [
      "DEVELOP"
    ]
  },
  {
    "lot_hist_id": "LH-00163",
    "lot_id": "LOT-260009",
    "wafer_no": 3,
    "chamber_id": "PHO-01-C1",
    "equipment_id": "PHO-01",
    "sensor_id": "PH_FOCUS",
    "recipe_id": "RCP-PH-A1",
    "occurred_at": "2026-06-03 22:30:30",
    "points": [
      {
        "seq_no": 1,
        "recipe_step_no": 1,
        "recipe_step_name": "EXPOSE",
        "measured_at": "2026-06-03 22:30:30",
        "value": 91.318
      },
      {
        "seq_no": 2,
        "recipe_step_no": 1,
        "recipe_step_name": "EXPOSE",
        "measured_at": "2026-06-03 22:30:50",
        "value": 70.254
      },
      {
        "seq_no": 3,
        "recipe_step_no": 1,
        "recipe_step_name": "EXPOSE",
        "measured_at": "2026-06-03 22:31:10",
        "value": 102.536
      }
    ]
  },
  {
    "lot_hist_id": "LH-00165",
    "lot_id": "LOT-260009",
    "wafer_no": 5,
    "chamber_id": "PHO-01-C1",
    "equipment_id": "PHO-01",
    "sensor_id": "PH_FOCUS",
    "recipe_id": "RCP-PH-A1",
    "occurred_at": "2026-06-03 22:34:27",
    "points": [
      {
        "seq_no": 1,
        "recipe_step_no": 1,
        "recipe_step_name": "EXPOSE",
        "measured_at": "2026-06-03 22:34:27",
        "value": 84.493
      },
      {
        "seq_no": 2,
        "recipe_step_no": 1,
        "recipe_step_name": "EXPOSE",
        "measured_at": "2026-06-03 22:34:47",
        "value": 79.448
      },
      {
        "seq_no": 3,
        "recipe_step_no": 1,
        "recipe_step_name": "EXPOSE",
        "measured_at": "2026-06-03 22:35:07",
        "value": 93.669
      }
    ]
  },
  {
    "lot_hist_id": "LH-00167",
    "lot_id": "LOT-260009",
    "wafer_no": 7,
    "chamber_id": "PHO-01-C1",
    "equipment_id": "PHO-01",
    "sensor_id": "PH_FOCUS",
    "recipe_id": "RCP-PH-A1",
    "occurred_at": "2026-06-03 22:38:35",
    "points": [
      {
        "seq_no": 1,
        "recipe_step_no": 1,
        "recipe_step_name": "EXPOSE",
        "measured_at": "2026-06-03 22:38:35",
        "value": 82.868
      },
      {
        "seq_no": 2,
        "recipe_step_no": 1,
        "recipe_step_name": "EXPOSE",
        "measured_at": "2026-06-03 22:38:55",
        "value": 80.393
      },
      {
        "seq_no": 3,
        "recipe_step_no": 1,
        "recipe_step_name": "EXPOSE",
        "measured_at": "2026-06-03 22:39:15",
        "value": 113.033
      },
      {
        "seq_no": 4,
        "recipe_step_no": 2,
        "recipe_step_name": "DEVELOP",
        "measured_at": "2026-06-03 22:39:35",
        "value": 26.419
      },
      {
        "seq_no": 5,
        "recipe_step_no": 2,
        "recipe_step_name": "DEVELOP",
        "measured_at": "2026-06-03 22:39:55",
        "value": 26.128
      },
      {
        "seq_no": 6,
        "recipe_step_no": 2,
        "recipe_step_name": "DEVELOP",
        "measured_at": "2026-06-03 22:40:15",
        "value": 65.002
      }
    ]
  },
  {
    "lot_hist_id": "LH-00169",
    "lot_id": "LOT-260009",
    "wafer_no": 9,
    "chamber_id": "PHO-01-C1",
    "equipment_id": "PHO-01",
    "sensor_id": "PH_FOCUS",
    "recipe_id": "RCP-PH-A1",
    "occurred_at": "2026-06-03 22:42:21",
    "points": [
      {
        "seq_no": 1,
        "recipe_step_no": 1,
        "recipe_step_name": "EXPOSE",
        "measured_at": "2026-06-03 22:42:21",
        "value": 101.448
      },
      {
        "seq_no": 2,
        "recipe_step_no": 1,
        "recipe_step_name": "EXPOSE",
        "measured_at": "2026-06-03 22:42:41",
        "value": 71.575
      },
      {
        "seq_no": 3,
        "recipe_step_no": 1,
        "recipe_step_name": "EXPOSE",
        "measured_at": "2026-06-03 22:43:01",
        "value": 103.793
      }
    ]
  },
  {
    "lot_hist_id": "LH-00191",
    "lot_id": "LOT-260010",
    "wafer_no": 1,
    "chamber_id": "ETC-01-C1",
    "equipment_id": "ETC-01",
    "sensor_id": "ET_CF4",
    "recipe_id": "RCP-ET-B1",
    "occurred_at": "2026-06-04 07:10:41",
    "points": [
      {
        "seq_no": 1,
        "recipe_step_no": 1,
        "recipe_step_name": "MAIN_ETCH",
        "measured_at": "2026-06-04 07:10:41",
        "value": 74.309
      },
      {
        "seq_no": 2,
        "recipe_step_no": 1,
        "recipe_step_name": "MAIN_ETCH",
        "measured_at": "2026-06-04 07:11:01",
        "value": 72.061
      },
      {
        "seq_no": 3,
        "recipe_step_no": 1,
        "recipe_step_name": "MAIN_ETCH",
        "measured_at": "2026-06-04 07:11:21",
        "value": 74.28
      }
    ]
  },
  {
    "lot_hist_id": "LH-00193",
    "lot_id": "LOT-260010",
    "wafer_no": 3,
    "chamber_id": "ETC-01-C1",
    "equipment_id": "ETC-01",
    "sensor_id": "ET_CF4",
    "recipe_id": "RCP-ET-B1",
    "occurred_at": "2026-06-04 07:15:24",
    "points": [
      {
        "seq_no": 1,
        "recipe_step_no": 1,
        "recipe_step_name": "MAIN_ETCH",
        "measured_at": "2026-06-04 07:15:24",
        "value": 73.24
      },
      {
        "seq_no": 2,
        "recipe_step_no": 1,
        "recipe_step_name": "MAIN_ETCH",
        "measured_at": "2026-06-04 07:15:44",
        "value": 71.527
      },
      {
        "seq_no": 3,
        "recipe_step_no": 1,
        "recipe_step_name": "MAIN_ETCH",
        "measured_at": "2026-06-04 07:16:04",
        "value": 73.474
      }
    ]
  },
  {
    "lot_hist_id": "LH-00195",
    "lot_id": "LOT-260010",
    "wafer_no": 5,
    "chamber_id": "ETC-01-C1",
    "equipment_id": "ETC-01",
    "sensor_id": "ET_CF4",
    "recipe_id": "RCP-ET-B1",
    "occurred_at": "2026-06-04 07:20:18",
    "points": [
      {
        "seq_no": 1,
        "recipe_step_no": 1,
        "recipe_step_name": "MAIN_ETCH",
        "measured_at": "2026-06-04 07:20:18",
        "value": 72.76
      },
      {
        "seq_no": 2,
        "recipe_step_no": 1,
        "recipe_step_name": "MAIN_ETCH",
        "measured_at": "2026-06-04 07:20:38",
        "value": 72.259
      },
      {
        "seq_no": 3,
        "recipe_step_no": 1,
        "recipe_step_name": "MAIN_ETCH",
        "measured_at": "2026-06-04 07:20:58",
        "value": 74.599
      }
    ]
  },
  {
    "lot_hist_id": "LH-00197",
    "lot_id": "LOT-260010",
    "wafer_no": 7,
    "chamber_id": "ETC-01-C1",
    "equipment_id": "ETC-01",
    "sensor_id": "ET_CF4",
    "recipe_id": "RCP-ET-B1",
    "occurred_at": "2026-06-04 07:25:02",
    "points": [
      {
        "seq_no": 1,
        "recipe_step_no": 1,
        "recipe_step_name": "MAIN_ETCH",
        "measured_at": "2026-06-04 07:25:02",
        "value": 72.573
      },
      {
        "seq_no": 2,
        "recipe_step_no": 1,
        "recipe_step_name": "MAIN_ETCH",
        "measured_at": "2026-06-04 07:25:22",
        "value": 71.384
      },
      {
        "seq_no": 3,
        "recipe_step_no": 1,
        "recipe_step_name": "MAIN_ETCH",
        "measured_at": "2026-06-04 07:25:42",
        "value": 73.972
      }
    ]
  },
  {
    "lot_hist_id": "LH-00199",
    "lot_id": "LOT-260010",
    "wafer_no": 9,
    "chamber_id": "ETC-01-C1",
    "equipment_id": "ETC-01",
    "sensor_id": "ET_CF4",
    "recipe_id": "RCP-ET-B1",
    "occurred_at": "2026-06-04 07:29:49",
    "points": [
      {
        "seq_no": 1,
        "recipe_step_no": 1,
        "recipe_step_name": "MAIN_ETCH",
        "measured_at": "2026-06-04 07:29:49",
        "value": 72.247
      },
      {
        "seq_no": 2,
        "recipe_step_no": 1,
        "recipe_step_name": "MAIN_ETCH",
        "measured_at": "2026-06-04 07:30:09",
        "value": 69.935
      },
      {
        "seq_no": 3,
        "recipe_step_no": 1,
        "recipe_step_name": "MAIN_ETCH",
        "measured_at": "2026-06-04 07:30:29",
        "value": 74.841
      }
    ]
  }
]
