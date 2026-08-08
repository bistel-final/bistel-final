// 대시보드 mock — 모든 값은 alarms-data.js 51건 / RUN 확정표 / 승인 큐 2건에서 파생한 실측 집계
// 스코프 키: 전체 · 공정(PHOTO·ETCH) · 장비(PHO-01·ETC-01) · 챔버 4개
// 계층은 도메인 규칙 "AREA > EQUIPMENT > CHAMBER"를 따른다
export const DASHBOARD = {
  date: '2026-06-04',
  hierarchy: [
    {
      "area": "PHOTO",
      "equipments": [
        {
          "id": "PHO-01",
          "model": "PH-9000",
          "chambers": [
            "PHO-01-C1",
            "PHO-01-C2"
          ]
        }
      ]
    },
    {
      "area": "ETCH",
      "equipments": [
        {
          "id": "ETC-01",
          "model": "ET-7500",
          "chambers": [
            "ETC-01-C1",
            "ETC-01-C2"
          ]
        }
      ]
    }
  ],
  scopes: {
    "전체": {
      "kpi": {
        "today": 6,
        "todayOos": 6,
        "todayOoc": 0,
        "total": 51,
        "totalOos": 37,
        "totalOoc": 14
      },
      "days": [
        {
          "label": "6/1",
          "oos": 0,
          "ooc": 4
        },
        {
          "label": "6/2",
          "oos": 11,
          "ooc": 3
        },
        {
          "label": "6/3",
          "oos": 20,
          "ooc": 7
        },
        {
          "label": "6/4",
          "oos": 6,
          "ooc": 0
        }
      ],
      "pending": 2,
      "active": 10,
      "recent": [
        {
          "id": "ALM-0051",
          "sensor": "ET_CF4",
          "eqp": "ETC-01-C1",
          "rule": "R01_OOS",
          "time": "07:29",
          "crit": false
        },
        {
          "id": "ALM-0050",
          "sensor": "ET_CF4",
          "eqp": "ETC-01-C1",
          "rule": "R01_OOS",
          "time": "07:25",
          "crit": false
        },
        {
          "id": "ALM-0049",
          "sensor": "ET_CF4",
          "eqp": "ETC-01-C1",
          "rule": "R01_OOS",
          "time": "07:20",
          "crit": false
        },
        {
          "id": "ALM-0048",
          "sensor": "ET_CF4",
          "eqp": "ETC-01-C1",
          "rule": "R03_CONSEC",
          "time": "07:15",
          "crit": true
        },
        {
          "id": "ALM-0047",
          "sensor": "ET_CF4",
          "eqp": "ETC-01-C1",
          "rule": "R01_OOS",
          "time": "07:15",
          "crit": false
        }
      ]
    },
    "PHOTO": {
      "kpi": {
        "today": 0,
        "todayOos": 0,
        "todayOoc": 0,
        "total": 22,
        "totalOos": 17,
        "totalOoc": 5
      },
      "days": [
        {
          "label": "6/1",
          "oos": 0,
          "ooc": 0
        },
        {
          "label": "6/2",
          "oos": 0,
          "ooc": 3
        },
        {
          "label": "6/3",
          "oos": 17,
          "ooc": 2
        },
        {
          "label": "6/4",
          "oos": 0,
          "ooc": 0
        }
      ],
      "pending": 1,
      "active": 4,
      "recent": [
        {
          "id": "ALM-0040",
          "sensor": "PH_FOCUS",
          "eqp": "PHO-01-C1",
          "rule": "R01_OOS",
          "time": "6/3 22:42",
          "crit": false
        },
        {
          "id": "ALM-0039",
          "sensor": "PH_FOCUS",
          "eqp": "PHO-01-C1",
          "rule": "R01_OOS",
          "time": "6/3 22:38",
          "crit": false
        },
        {
          "id": "ALM-0038",
          "sensor": "PH_FOCUS",
          "eqp": "PHO-01-C1",
          "rule": "R01_OOS",
          "time": "6/3 22:38",
          "crit": false
        },
        {
          "id": "ALM-0037",
          "sensor": "PH_FOCUS",
          "eqp": "PHO-01-C1",
          "rule": "R01_OOS",
          "time": "6/3 22:34",
          "crit": false
        },
        {
          "id": "ALM-0036",
          "sensor": "PH_FOCUS",
          "eqp": "PHO-01-C1",
          "rule": "R01_OOS",
          "time": "6/3 22:30",
          "crit": false
        }
      ]
    },
    "ETCH": {
      "kpi": {
        "today": 6,
        "todayOos": 6,
        "todayOoc": 0,
        "total": 29,
        "totalOos": 20,
        "totalOoc": 9
      },
      "days": [
        {
          "label": "6/1",
          "oos": 0,
          "ooc": 4
        },
        {
          "label": "6/2",
          "oos": 11,
          "ooc": 0
        },
        {
          "label": "6/3",
          "oos": 3,
          "ooc": 5
        },
        {
          "label": "6/4",
          "oos": 6,
          "ooc": 0
        }
      ],
      "pending": 1,
      "active": 6,
      "recent": [
        {
          "id": "ALM-0051",
          "sensor": "ET_CF4",
          "eqp": "ETC-01-C1",
          "rule": "R01_OOS",
          "time": "07:29",
          "crit": false
        },
        {
          "id": "ALM-0050",
          "sensor": "ET_CF4",
          "eqp": "ETC-01-C1",
          "rule": "R01_OOS",
          "time": "07:25",
          "crit": false
        },
        {
          "id": "ALM-0049",
          "sensor": "ET_CF4",
          "eqp": "ETC-01-C1",
          "rule": "R01_OOS",
          "time": "07:20",
          "crit": false
        },
        {
          "id": "ALM-0048",
          "sensor": "ET_CF4",
          "eqp": "ETC-01-C1",
          "rule": "R03_CONSEC",
          "time": "07:15",
          "crit": true
        },
        {
          "id": "ALM-0047",
          "sensor": "ET_CF4",
          "eqp": "ETC-01-C1",
          "rule": "R01_OOS",
          "time": "07:15",
          "crit": false
        }
      ]
    },
    "PHO-01": {
      "kpi": {
        "today": 0,
        "todayOos": 0,
        "todayOoc": 0,
        "total": 22,
        "totalOos": 17,
        "totalOoc": 5
      },
      "days": [
        {
          "label": "6/1",
          "oos": 0,
          "ooc": 0
        },
        {
          "label": "6/2",
          "oos": 0,
          "ooc": 3
        },
        {
          "label": "6/3",
          "oos": 17,
          "ooc": 2
        },
        {
          "label": "6/4",
          "oos": 0,
          "ooc": 0
        }
      ],
      "pending": 1,
      "active": 4,
      "recent": [
        {
          "id": "ALM-0040",
          "sensor": "PH_FOCUS",
          "eqp": "PHO-01-C1",
          "rule": "R01_OOS",
          "time": "6/3 22:42",
          "crit": false
        },
        {
          "id": "ALM-0039",
          "sensor": "PH_FOCUS",
          "eqp": "PHO-01-C1",
          "rule": "R01_OOS",
          "time": "6/3 22:38",
          "crit": false
        },
        {
          "id": "ALM-0038",
          "sensor": "PH_FOCUS",
          "eqp": "PHO-01-C1",
          "rule": "R01_OOS",
          "time": "6/3 22:38",
          "crit": false
        },
        {
          "id": "ALM-0037",
          "sensor": "PH_FOCUS",
          "eqp": "PHO-01-C1",
          "rule": "R01_OOS",
          "time": "6/3 22:34",
          "crit": false
        },
        {
          "id": "ALM-0036",
          "sensor": "PH_FOCUS",
          "eqp": "PHO-01-C1",
          "rule": "R01_OOS",
          "time": "6/3 22:30",
          "crit": false
        }
      ]
    },
    "ETC-01": {
      "kpi": {
        "today": 6,
        "todayOos": 6,
        "todayOoc": 0,
        "total": 29,
        "totalOos": 20,
        "totalOoc": 9
      },
      "days": [
        {
          "label": "6/1",
          "oos": 0,
          "ooc": 4
        },
        {
          "label": "6/2",
          "oos": 11,
          "ooc": 0
        },
        {
          "label": "6/3",
          "oos": 3,
          "ooc": 5
        },
        {
          "label": "6/4",
          "oos": 6,
          "ooc": 0
        }
      ],
      "pending": 1,
      "active": 6,
      "recent": [
        {
          "id": "ALM-0051",
          "sensor": "ET_CF4",
          "eqp": "ETC-01-C1",
          "rule": "R01_OOS",
          "time": "07:29",
          "crit": false
        },
        {
          "id": "ALM-0050",
          "sensor": "ET_CF4",
          "eqp": "ETC-01-C1",
          "rule": "R01_OOS",
          "time": "07:25",
          "crit": false
        },
        {
          "id": "ALM-0049",
          "sensor": "ET_CF4",
          "eqp": "ETC-01-C1",
          "rule": "R01_OOS",
          "time": "07:20",
          "crit": false
        },
        {
          "id": "ALM-0048",
          "sensor": "ET_CF4",
          "eqp": "ETC-01-C1",
          "rule": "R03_CONSEC",
          "time": "07:15",
          "crit": true
        },
        {
          "id": "ALM-0047",
          "sensor": "ET_CF4",
          "eqp": "ETC-01-C1",
          "rule": "R01_OOS",
          "time": "07:15",
          "crit": false
        }
      ]
    },
    "PHO-01-C1": {
      "kpi": {
        "today": 0,
        "todayOos": 0,
        "todayOoc": 0,
        "total": 22,
        "totalOos": 17,
        "totalOoc": 5
      },
      "days": [
        {
          "label": "6/1",
          "oos": 0,
          "ooc": 0
        },
        {
          "label": "6/2",
          "oos": 0,
          "ooc": 3
        },
        {
          "label": "6/3",
          "oos": 17,
          "ooc": 2
        },
        {
          "label": "6/4",
          "oos": 0,
          "ooc": 0
        }
      ],
      "pending": 1,
      "active": 4,
      "recent": [
        {
          "id": "ALM-0040",
          "sensor": "PH_FOCUS",
          "eqp": "PHO-01-C1",
          "rule": "R01_OOS",
          "time": "6/3 22:42",
          "crit": false
        },
        {
          "id": "ALM-0039",
          "sensor": "PH_FOCUS",
          "eqp": "PHO-01-C1",
          "rule": "R01_OOS",
          "time": "6/3 22:38",
          "crit": false
        },
        {
          "id": "ALM-0038",
          "sensor": "PH_FOCUS",
          "eqp": "PHO-01-C1",
          "rule": "R01_OOS",
          "time": "6/3 22:38",
          "crit": false
        },
        {
          "id": "ALM-0037",
          "sensor": "PH_FOCUS",
          "eqp": "PHO-01-C1",
          "rule": "R01_OOS",
          "time": "6/3 22:34",
          "crit": false
        },
        {
          "id": "ALM-0036",
          "sensor": "PH_FOCUS",
          "eqp": "PHO-01-C1",
          "rule": "R01_OOS",
          "time": "6/3 22:30",
          "crit": false
        }
      ]
    },
    "PHO-01-C2": {
      "kpi": {
        "today": 0,
        "todayOos": 0,
        "todayOoc": 0,
        "total": 0,
        "totalOos": 0,
        "totalOoc": 0
      },
      "days": [
        {
          "label": "6/1",
          "oos": 0,
          "ooc": 0
        },
        {
          "label": "6/2",
          "oos": 0,
          "ooc": 0
        },
        {
          "label": "6/3",
          "oos": 0,
          "ooc": 0
        },
        {
          "label": "6/4",
          "oos": 0,
          "ooc": 0
        }
      ],
      "pending": 0,
      "active": 0,
      "recent": []
    },
    "ETC-01-C1": {
      "kpi": {
        "today": 6,
        "todayOos": 6,
        "todayOoc": 0,
        "total": 14,
        "totalOos": 9,
        "totalOoc": 5
      },
      "days": [
        {
          "label": "6/1",
          "oos": 0,
          "ooc": 0
        },
        {
          "label": "6/2",
          "oos": 0,
          "ooc": 0
        },
        {
          "label": "6/3",
          "oos": 3,
          "ooc": 5
        },
        {
          "label": "6/4",
          "oos": 6,
          "ooc": 0
        }
      ],
      "pending": 1,
      "active": 3,
      "recent": [
        {
          "id": "ALM-0051",
          "sensor": "ET_CF4",
          "eqp": "ETC-01-C1",
          "rule": "R01_OOS",
          "time": "07:29",
          "crit": false
        },
        {
          "id": "ALM-0050",
          "sensor": "ET_CF4",
          "eqp": "ETC-01-C1",
          "rule": "R01_OOS",
          "time": "07:25",
          "crit": false
        },
        {
          "id": "ALM-0049",
          "sensor": "ET_CF4",
          "eqp": "ETC-01-C1",
          "rule": "R01_OOS",
          "time": "07:20",
          "crit": false
        },
        {
          "id": "ALM-0048",
          "sensor": "ET_CF4",
          "eqp": "ETC-01-C1",
          "rule": "R03_CONSEC",
          "time": "07:15",
          "crit": true
        },
        {
          "id": "ALM-0047",
          "sensor": "ET_CF4",
          "eqp": "ETC-01-C1",
          "rule": "R01_OOS",
          "time": "07:15",
          "crit": false
        }
      ]
    },
    "ETC-01-C2": {
      "kpi": {
        "today": 0,
        "todayOos": 0,
        "todayOoc": 0,
        "total": 15,
        "totalOos": 11,
        "totalOoc": 4
      },
      "days": [
        {
          "label": "6/1",
          "oos": 0,
          "ooc": 4
        },
        {
          "label": "6/2",
          "oos": 11,
          "ooc": 0
        },
        {
          "label": "6/3",
          "oos": 0,
          "ooc": 0
        },
        {
          "label": "6/4",
          "oos": 0,
          "ooc": 0
        }
      ],
      "pending": 0,
      "active": 3,
      "recent": [
        {
          "id": "ALM-0015",
          "sensor": "ET_REFL",
          "eqp": "ETC-01-C2",
          "rule": "R01_OOS",
          "time": "6/2 15:39",
          "crit": false
        },
        {
          "id": "ALM-0014",
          "sensor": "ET_REFL",
          "eqp": "ETC-01-C2",
          "rule": "R01_OOS",
          "time": "6/2 15:34",
          "crit": false
        },
        {
          "id": "ALM-0013",
          "sensor": "ET_REFL",
          "eqp": "ETC-01-C2",
          "rule": "R01_OOS",
          "time": "6/2 15:29",
          "crit": false
        },
        {
          "id": "ALM-0012",
          "sensor": "ET_REFL",
          "eqp": "ETC-01-C2",
          "rule": "R01_OOS",
          "time": "6/2 15:24",
          "crit": false
        },
        {
          "id": "ALM-0011",
          "sensor": "ET_REFL",
          "eqp": "ETC-01-C2",
          "rule": "R01_OOS",
          "time": "6/2 15:19",
          "crit": false
        }
      ]
    }
  },
}
