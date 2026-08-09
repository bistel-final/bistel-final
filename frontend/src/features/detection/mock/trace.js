// 센서 Trace fixture
// TODO(api): GET /traces/{lot_hist_id} 미구현 — fdc_trace.csv 연결 시 이 파일만 교체
//
// 포인트 복원식: 스텝 3포인트 = [3*mean − min − max, min, max]
//   alarm detail의 mean·min·max에서 역산. 지시서 §2의 W1·3·5 EXPOSE 재집계값
//   (mean 57.240 / std 8.589 / min 44.616 / max 69.377)과 정확히 일치함을 검증했다.
//   OOC 알람은 detail에 mean만 있어 복원 불가 → points: null (해당 스텝 "실측 미제공" 표기)
// LOT-260007 W5 DEVELOP 3점은 지시서 §2가 직접 제공한 실측값.

export const TRACE_LIMITS = [
  { label: 'USL', value: 60 },
  { label: 'UCL', value: 36 },
  { label: 'TARGET', value: 0 },
  { label: 'LCL', value: -36 },
  { label: 'LSL', value: -60 },
]

// 판정에는 쓰지 않는 참고 지표 (§2: 0.87은 confidence와 혼동된 값)
export const TRACE_ANOMALY = { score: 0.84, threshold: 0.62 }

// §2가 직접 제공한 구간 통계 — 알람이 없어 포인트 복원이 불가능한 조합
// 키: "LOT|챔버|파라미터|웨이퍼목록"
export const MEASURED_STEP_STATS = {
  'LOT-260007|PHO-01-C1|PH_FOCUS|1,3,5': {
    DEVELOP: { mean: 16.612, std: 10.926, min: 0.144, max: 38.309 },
  },
}

export const WAFER_TRACES = [
  {
    "lot_id": "LOT-260003",
    "lot_hist_id": "LH-00052",
    "chamber_id": "ETC-01-C2",
    "sensor_id": "ET_REFL",
    "wafer_no": "2",
    "occurred_at": "2026-06-01 23:17:23",
    "steps": {
      "MAIN_ETCH": {
        "points": null,
        "mean": 22.46
      }
    }
  },
  {
    "lot_id": "LOT-260003",
    "lot_hist_id": "LH-00054",
    "chamber_id": "ETC-01-C2",
    "sensor_id": "ET_REFL",
    "wafer_no": "4",
    "occurred_at": "2026-06-01 23:22:23",
    "steps": {
      "MAIN_ETCH": {
        "points": null,
        "mean": 21.496
      }
    }
  },
  {
    "lot_id": "LOT-260003",
    "lot_hist_id": "LH-00058",
    "chamber_id": "ETC-01-C2",
    "sensor_id": "ET_REFL",
    "wafer_no": "8",
    "occurred_at": "2026-06-01 23:32:09",
    "steps": {
      "MAIN_ETCH": {
        "points": null,
        "mean": 21.962
      }
    }
  },
  {
    "lot_id": "LOT-260003",
    "lot_hist_id": "LH-00060",
    "chamber_id": "ETC-01-C2",
    "sensor_id": "ET_REFL",
    "wafer_no": "10",
    "occurred_at": "2026-06-01 23:36:49",
    "steps": {
      "MAIN_ETCH": {
        "points": null,
        "mean": 19.679
      }
    }
  },
  {
    "lot_id": "LOT-260004",
    "lot_hist_id": "LH-00072",
    "chamber_id": "ETC-01-C2",
    "sensor_id": "ET_REFL",
    "wafer_no": "2",
    "occurred_at": "2026-06-02 07:20:23",
    "steps": {
      "MAIN_ETCH": {
        "points": [
          32.685,
          26.7,
          34.203
        ]
      }
    }
  },
  {
    "lot_id": "LOT-260004",
    "lot_hist_id": "LH-00074",
    "chamber_id": "ETC-01-C2",
    "sensor_id": "ET_REFL",
    "wafer_no": "4",
    "occurred_at": "2026-06-02 07:25:36",
    "steps": {
      "MAIN_ETCH": {
        "points": [
          30.784,
          26.602,
          38.128
        ]
      }
    }
  },
  {
    "lot_id": "LOT-260004",
    "lot_hist_id": "LH-00076",
    "chamber_id": "ETC-01-C2",
    "sensor_id": "ET_REFL",
    "wafer_no": "6",
    "occurred_at": "2026-06-02 07:30:49",
    "steps": {
      "MAIN_ETCH": {
        "points": [
          27.956,
          26.495,
          31.67
        ]
      }
    }
  },
  {
    "lot_id": "LOT-260004",
    "lot_hist_id": "LH-00078",
    "chamber_id": "ETC-01-C2",
    "sensor_id": "ET_REFL",
    "wafer_no": "8",
    "occurred_at": "2026-06-02 07:35:44",
    "steps": {
      "MAIN_ETCH": {
        "points": [
          28.164,
          23.074,
          33.119
        ]
      }
    }
  },
  {
    "lot_id": "LOT-260004",
    "lot_hist_id": "LH-00080",
    "chamber_id": "ETC-01-C2",
    "sensor_id": "ET_REFL",
    "wafer_no": "10",
    "occurred_at": "2026-06-02 07:40:26",
    "steps": {
      "MAIN_ETCH": {
        "points": [
          28.588,
          22.289,
          34.047
        ]
      }
    }
  },
  {
    "lot_id": "LOT-260005",
    "lot_hist_id": "LH-00092",
    "chamber_id": "ETC-01-C2",
    "sensor_id": "ET_REFL",
    "wafer_no": "2",
    "occurred_at": "2026-06-02 15:19:49",
    "steps": {
      "MAIN_ETCH": {
        "points": [
          42.012,
          26.919,
          43.386
        ]
      }
    }
  },
  {
    "lot_id": "LOT-260005",
    "lot_hist_id": "LH-00094",
    "chamber_id": "ETC-01-C2",
    "sensor_id": "ET_REFL",
    "wafer_no": "4",
    "occurred_at": "2026-06-02 15:24:38",
    "steps": {
      "MAIN_ETCH": {
        "points": [
          40.425,
          36.347,
          45.832
        ]
      }
    }
  },
  {
    "lot_id": "LOT-260005",
    "lot_hist_id": "LH-00096",
    "chamber_id": "ETC-01-C2",
    "sensor_id": "ET_REFL",
    "wafer_no": "6",
    "occurred_at": "2026-06-02 15:29:45",
    "steps": {
      "MAIN_ETCH": {
        "points": [
          40.192,
          34.439,
          43.617
        ]
      }
    }
  },
  {
    "lot_id": "LOT-260005",
    "lot_hist_id": "LH-00098",
    "chamber_id": "ETC-01-C2",
    "sensor_id": "ET_REFL",
    "wafer_no": "8",
    "occurred_at": "2026-06-02 15:34:43",
    "steps": {
      "MAIN_ETCH": {
        "points": [
          39.581,
          36.551,
          43.982
        ]
      }
    }
  },
  {
    "lot_id": "LOT-260005",
    "lot_hist_id": "LH-00100",
    "chamber_id": "ETC-01-C2",
    "sensor_id": "ET_REFL",
    "wafer_no": "10",
    "occurred_at": "2026-06-02 15:39:42",
    "steps": {
      "MAIN_ETCH": {
        "points": [
          35.135,
          29.984,
          44.756
        ]
      }
    }
  },
  {
    "lot_id": "LOT-260006",
    "lot_hist_id": "LH-00101",
    "chamber_id": "PHO-01-C1",
    "sensor_id": "PH_FOCUS",
    "wafer_no": "1",
    "occurred_at": "2026-06-02 22:38:32",
    "steps": {
      "EXPOSE": {
        "points": null,
        "mean": 36.755
      }
    }
  },
  {
    "lot_id": "LOT-260006",
    "lot_hist_id": "LH-00103",
    "chamber_id": "PHO-01-C1",
    "sensor_id": "PH_FOCUS",
    "wafer_no": "3",
    "occurred_at": "2026-06-02 22:42:30",
    "steps": {
      "EXPOSE": {
        "points": null,
        "mean": 36.027
      }
    }
  },
  {
    "lot_id": "LOT-260006",
    "lot_hist_id": "LH-00109",
    "chamber_id": "PHO-01-C1",
    "sensor_id": "PH_FOCUS",
    "wafer_no": "9",
    "occurred_at": "2026-06-02 22:54:15",
    "steps": {
      "EXPOSE": {
        "points": null,
        "mean": 45.558
      }
    }
  },
  {
    "lot_id": "LOT-260007",
    "lot_hist_id": "LH-00121",
    "chamber_id": "PHO-01-C1",
    "sensor_id": "PH_FOCUS",
    "wafer_no": "1",
    "occurred_at": "2026-06-03 06:37:47",
    "steps": {
      "EXPOSE": {
        "points": [
          57.315,
          47.995,
          67.226
        ]
      }
    }
  },
  {
    "lot_id": "LOT-260007",
    "lot_hist_id": "LH-00123",
    "chamber_id": "PHO-01-C1",
    "sensor_id": "PH_FOCUS",
    "wafer_no": "3",
    "occurred_at": "2026-06-03 06:41:45",
    "steps": {
      "EXPOSE": {
        "points": [
          56.335,
          44.616,
          69.377
        ]
      }
    }
  },
  {
    "lot_id": "LOT-260007",
    "lot_hist_id": "LH-00125",
    "chamber_id": "PHO-01-C1",
    "sensor_id": "PH_FOCUS",
    "wafer_no": "5",
    "occurred_at": "2026-06-03 06:45:45",
    "steps": {
      "EXPOSE": {
        "points": [
          54.752,
          52.193,
          65.353
        ]
      },
      "DEVELOP": {
        "points": [
          11.934,
          6.294,
          13.275
        ]
      }
    }
  },
  {
    "lot_id": "LOT-260007",
    "lot_hist_id": "LH-00127",
    "chamber_id": "PHO-01-C1",
    "sensor_id": "PH_FOCUS",
    "wafer_no": "7",
    "occurred_at": "2026-06-03 06:49:46",
    "steps": {
      "EXPOSE": {
        "points": [
          73.688,
          56.816,
          79.859
        ]
      }
    }
  },
  {
    "lot_id": "LOT-260007",
    "lot_hist_id": "LH-00129",
    "chamber_id": "PHO-01-C1",
    "sensor_id": "PH_FOCUS",
    "wafer_no": "9",
    "occurred_at": "2026-06-03 06:53:46",
    "steps": {
      "EXPOSE": {
        "points": [
          59.394,
          29.038,
          65.924
        ]
      }
    }
  },
  {
    "lot_id": "LOT-260008",
    "lot_hist_id": "LH-00153",
    "chamber_id": "ETC-01-C1",
    "sensor_id": "ET_CF4",
    "wafer_no": "3",
    "occurred_at": "2026-06-03 15:43:05",
    "steps": {
      "MAIN_ETCH": {
        "points": null,
        "mean": 76.934
      }
    }
  },
  {
    "lot_id": "LOT-260008",
    "lot_hist_id": "LH-00155",
    "chamber_id": "ETC-01-C1",
    "sensor_id": "ET_CF4",
    "wafer_no": "5",
    "occurred_at": "2026-06-03 15:48:09",
    "steps": {
      "MAIN_ETCH": {
        "points": null,
        "mean": 76.306
      }
    }
  },
  {
    "lot_id": "LOT-260008",
    "lot_hist_id": "LH-00157",
    "chamber_id": "ETC-01-C1",
    "sensor_id": "ET_CF4",
    "wafer_no": "7",
    "occurred_at": "2026-06-03 15:52:49",
    "steps": {
      "MAIN_ETCH": {
        "points": null,
        "mean": 76.393
      }
    }
  },
  {
    "lot_id": "LOT-260008",
    "lot_hist_id": "LH-00141",
    "chamber_id": "PHO-01-C1",
    "sensor_id": "PH_FOCUS",
    "wafer_no": "1",
    "occurred_at": "2026-06-03 14:39:36",
    "steps": {
      "EXPOSE": {
        "points": [
          58.116,
          50.323,
          64.16
        ]
      },
      "DEVELOP": {
        "points": null,
        "mean": 42.95
      }
    }
  },
  {
    "lot_id": "LOT-260008",
    "lot_hist_id": "LH-00143",
    "chamber_id": "PHO-01-C1",
    "sensor_id": "PH_FOCUS",
    "wafer_no": "3",
    "occurred_at": "2026-06-03 14:43:36",
    "steps": {
      "EXPOSE": {
        "points": [
          87.318,
          68.756,
          98.029
        ]
      }
    }
  },
  {
    "lot_id": "LOT-260008",
    "lot_hist_id": "LH-00145",
    "chamber_id": "PHO-01-C1",
    "sensor_id": "PH_FOCUS",
    "wafer_no": "5",
    "occurred_at": "2026-06-03 14:47:13",
    "steps": {
      "EXPOSE": {
        "points": [
          69.123,
          49.917,
          78.465
        ]
      }
    }
  },
  {
    "lot_id": "LOT-260008",
    "lot_hist_id": "LH-00147",
    "chamber_id": "PHO-01-C1",
    "sensor_id": "PH_FOCUS",
    "wafer_no": "7",
    "occurred_at": "2026-06-03 14:51:08",
    "steps": {
      "EXPOSE": {
        "points": [
          63.262,
          56.276,
          91.671
        ]
      }
    }
  },
  {
    "lot_id": "LOT-260008",
    "lot_hist_id": "LH-00149",
    "chamber_id": "PHO-01-C1",
    "sensor_id": "PH_FOCUS",
    "wafer_no": "9",
    "occurred_at": "2026-06-03 14:54:42",
    "steps": {
      "EXPOSE": {
        "points": [
          73.964,
          54.221,
          79.835
        ]
      }
    }
  },
  {
    "lot_id": "LOT-260009",
    "lot_hist_id": "LH-00171",
    "chamber_id": "ETC-01-C1",
    "sensor_id": "ET_CF4",
    "wafer_no": "1",
    "occurred_at": "2026-06-03 23:12:41",
    "steps": {
      "MAIN_ETCH": {
        "points": [
          75.443,
          73.667,
          76.796
        ]
      }
    }
  },
  {
    "lot_id": "LOT-260009",
    "lot_hist_id": "LH-00173",
    "chamber_id": "ETC-01-C1",
    "sensor_id": "ET_CF4",
    "wafer_no": "3",
    "occurred_at": "2026-06-03 23:17:24",
    "steps": {
      "MAIN_ETCH": {
        "points": [
          73.369,
          73.586,
          75.243
        ]
      }
    }
  },
  {
    "lot_id": "LOT-260009",
    "lot_hist_id": "LH-00175",
    "chamber_id": "ETC-01-C1",
    "sensor_id": "ET_CF4",
    "wafer_no": "5",
    "occurred_at": "2026-06-03 23:22:20",
    "steps": {
      "MAIN_ETCH": {
        "points": null,
        "mean": 76.166
      }
    }
  },
  {
    "lot_id": "LOT-260009",
    "lot_hist_id": "LH-00177",
    "chamber_id": "ETC-01-C1",
    "sensor_id": "ET_CF4",
    "wafer_no": "7",
    "occurred_at": "2026-06-03 23:27:11",
    "steps": {
      "MAIN_ETCH": {
        "points": null,
        "mean": 76.507
      }
    }
  },
  {
    "lot_id": "LOT-260009",
    "lot_hist_id": "LH-00179",
    "chamber_id": "ETC-01-C1",
    "sensor_id": "ET_CF4",
    "wafer_no": "9",
    "occurred_at": "2026-06-03 23:31:50",
    "steps": {
      "MAIN_ETCH": {
        "points": [
          74.352,
          73.438,
          74.723
        ]
      }
    }
  },
  {
    "lot_id": "LOT-260009",
    "lot_hist_id": "LH-00161",
    "chamber_id": "PHO-01-C1",
    "sensor_id": "PH_FOCUS",
    "wafer_no": "1",
    "occurred_at": "2026-06-03 22:26:40",
    "steps": {
      "EXPOSE": {
        "points": [
          99.816,
          67.438,
          114.935
        ]
      },
      "DEVELOP": {
        "points": null,
        "mean": 31.737
      }
    }
  },
  {
    "lot_id": "LOT-260009",
    "lot_hist_id": "LH-00163",
    "chamber_id": "PHO-01-C1",
    "sensor_id": "PH_FOCUS",
    "wafer_no": "3",
    "occurred_at": "2026-06-03 22:30:30",
    "steps": {
      "EXPOSE": {
        "points": [
          91.318,
          70.254,
          102.536
        ]
      }
    }
  },
  {
    "lot_id": "LOT-260009",
    "lot_hist_id": "LH-00165",
    "chamber_id": "PHO-01-C1",
    "sensor_id": "PH_FOCUS",
    "wafer_no": "5",
    "occurred_at": "2026-06-03 22:34:27",
    "steps": {
      "EXPOSE": {
        "points": [
          84.493,
          79.448,
          93.669
        ]
      }
    }
  },
  {
    "lot_id": "LOT-260009",
    "lot_hist_id": "LH-00167",
    "chamber_id": "PHO-01-C1",
    "sensor_id": "PH_FOCUS",
    "wafer_no": "7",
    "occurred_at": "2026-06-03 22:38:35",
    "steps": {
      "EXPOSE": {
        "points": [
          82.868,
          80.393,
          113.033
        ]
      },
      "DEVELOP": {
        "points": [
          26.419,
          26.128,
          65.002
        ]
      }
    }
  },
  {
    "lot_id": "LOT-260009",
    "lot_hist_id": "LH-00169",
    "chamber_id": "PHO-01-C1",
    "sensor_id": "PH_FOCUS",
    "wafer_no": "9",
    "occurred_at": "2026-06-03 22:42:21",
    "steps": {
      "EXPOSE": {
        "points": [
          101.448,
          71.575,
          103.793
        ]
      }
    }
  },
  {
    "lot_id": "LOT-260010",
    "lot_hist_id": "LH-00191",
    "chamber_id": "ETC-01-C1",
    "sensor_id": "ET_CF4",
    "wafer_no": "1",
    "occurred_at": "2026-06-04 07:10:41",
    "steps": {
      "MAIN_ETCH": {
        "points": [
          74.309,
          72.061,
          74.28
        ]
      }
    }
  },
  {
    "lot_id": "LOT-260010",
    "lot_hist_id": "LH-00193",
    "chamber_id": "ETC-01-C1",
    "sensor_id": "ET_CF4",
    "wafer_no": "3",
    "occurred_at": "2026-06-04 07:15:24",
    "steps": {
      "MAIN_ETCH": {
        "points": [
          73.24,
          71.527,
          73.474
        ]
      }
    }
  },
  {
    "lot_id": "LOT-260010",
    "lot_hist_id": "LH-00195",
    "chamber_id": "ETC-01-C1",
    "sensor_id": "ET_CF4",
    "wafer_no": "5",
    "occurred_at": "2026-06-04 07:20:18",
    "steps": {
      "MAIN_ETCH": {
        "points": [
          72.76,
          72.259,
          74.599
        ]
      }
    }
  },
  {
    "lot_id": "LOT-260010",
    "lot_hist_id": "LH-00197",
    "chamber_id": "ETC-01-C1",
    "sensor_id": "ET_CF4",
    "wafer_no": "7",
    "occurred_at": "2026-06-04 07:25:02",
    "steps": {
      "MAIN_ETCH": {
        "points": [
          72.573,
          71.384,
          73.972
        ]
      }
    }
  },
  {
    "lot_id": "LOT-260010",
    "lot_hist_id": "LH-00199",
    "chamber_id": "ETC-01-C1",
    "sensor_id": "ET_CF4",
    "wafer_no": "9",
    "occurred_at": "2026-06-04 07:29:49",
    "steps": {
      "MAIN_ETCH": {
        "points": [
          72.247,
          69.935,
          74.841
        ]
      }
    }
  }
]
