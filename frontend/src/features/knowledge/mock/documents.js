// dc.html 문서 검색 mock — 예시 질문 3건, top_k=4 결과 카드
export const DOC_FILTERS = ['전체', 'PH-9000', 'ET-7500']

export const DOC_CHIPS = [
  {
    group: 'Recommended',
    items: [
      {
        label: 'R01과 R03는 어떤 기준으로 구분하나요?',
        query: 'R01과 R03는 어떤 기준으로 구분하나요',
        model_codes: ['PH-9000', 'ET-7500'],
        doc_types: ['TROUBLESHOOT'],
        priority: 90,
      },
      {
        label: 'R03가 발생하면 왜 EQP_HOLD 승인이 필요한가요?',
        query: 'R03가 발생하면 왜 EQP_HOLD 승인이 필요한가요',
        model_codes: ['PH-9000', 'ET-7500'],
        doc_types: ['TROUBLESHOOT'],
        priority: 88,
      },
      {
        label: 'ETCH CD 이상일 때 PHOTO 영향은 어떻게 확인하나요?',
        query: 'ETCH CD 이상일 때 PHOTO 영향은 어떻게 확인하나요',
        model_codes: ['PH-9000', 'ET-7500'],
        doc_types: ['SPEC', 'TROUBLESHOOT'],
        priority: 80,
      },
      {
        label: 'PH-9000에서 포커스 이탈이 있으면 무엇을 먼저 점검하나요?',
        query: 'PH-9000에서 포커스 이탈이 있으면 무엇을 먼저 점검하나요',
        scoped_label: '포커스 이탈이 있으면 무엇을 먼저 점검하나요?',
        scoped_query: '포커스 이탈이 있으면 무엇을 먼저 점검하나요',
        model_codes: ['PH-9000'],
        doc_types: ['SPEC', 'TROUBLESHOOT'],
        priority: 100,
      },
      {
        label: 'PH-9000 노광량과 현상 온도 기준 범위는 어떻게 되나요?',
        query: 'PH-9000 노광량과 현상 온도 기준 범위는 어떻게 되나요',
        scoped_label: '노광량과 현상 온도 기준 범위는 어떻게 되나요?',
        scoped_query: '노광량과 현상 온도 기준 범위는 어떻게 되나요',
        model_codes: ['PH-9000'],
        doc_types: ['SPEC'],
        priority: 96,
      },
      {
        label: 'PH-9000 한 챔버에 이상이 집중되면 어디를 봐야 하나요?',
        query: 'PH-9000 한 챔버에 이상이 집중되면 어디를 봐야 하나요',
        scoped_label: '한 챔버에 이상이 집중되면 어디를 봐야 하나요?',
        scoped_query: '한 챔버에 이상이 집중되면 어디를 봐야 하나요',
        model_codes: ['PH-9000'],
        doc_types: ['SPEC'],
        priority: 92,
      },
      {
        label: 'ET-7500에서 반사파가 올라가면 무엇을 점검하나요?',
        query: 'ET-7500에서 반사파가 올라가면 무엇을 점검하나요',
        scoped_label: '반사파가 올라가면 무엇을 점검하나요?',
        scoped_query: '반사파가 올라가면 무엇을 점검하나요',
        model_codes: ['ET-7500'],
        doc_types: ['TROUBLESHOOT', 'SPEC'],
        priority: 100,
      },
      {
        label: 'ET-7500 CF4 유량 이상이면 어떤 원인을 확인하나요?',
        query: 'ET-7500 CF4 유량 이상이면 어떤 원인을 확인하나요',
        scoped_label: 'CF4 유량 이상이면 어떤 원인을 확인하나요?',
        scoped_query: 'CF4 유량 이상이면 어떤 원인을 확인하나요',
        model_codes: ['ET-7500'],
        doc_types: ['SPEC', 'TROUBLESHOOT'],
        priority: 96,
      },
      {
        label: 'ET-7500 정전척 온도 이탈은 무엇을 봐야 하나요?',
        query: 'ET-7500 정전척 온도 이탈은 무엇을 봐야 하나요',
        scoped_label: '정전척 온도 이탈은 무엇을 봐야 하나요?',
        scoped_query: '정전척 온도 이탈은 무엇을 봐야 하나요',
        model_codes: ['ET-7500'],
        doc_types: ['SPEC', 'TROUBLESHOOT'],
        priority: 94,
      },
      {
        label: 'ET-7500 챔버 압력 운전 기준은 어떻게 되나요?',
        query: 'ET-7500 챔버 압력 운전 기준은 어떻게 되나요',
        scoped_label: '챔버 압력 운전 기준은 어떻게 되나요?',
        scoped_query: '챔버 압력 운전 기준은 어떻게 되나요',
        model_codes: ['ET-7500'],
        doc_types: ['SPEC'],
        priority: 92,
      },
    ],
  },
]

export const DOC_SCORES = [0.88, 0.83, 0.79, 0.74]

export const DOC_DB = {
  'R01과 R03는 어떤 기준으로 구분하나요': [
    {
      doc: 'TROUBLE_FDC_FaultGuide',
      model: 'COMMON',
      section: '1. 이상 상태 이해하기',
      excerpt:
        'R01은 원시 측정 한 점이 허용 규격을 벗어난 상태이고, R03는 같은 챔버·파라미터·공정 단계의 OOS가 연속 3개 웨이퍼에 도달한 상태다.',
    },
    {
      doc: 'TROUBLE_FDC_FaultGuide',
      model: 'COMMON',
      section: '1.2 R03 — 반복 OOS',
      excerpt:
        'R03는 같은 챔버·파라미터·공정 단계의 TRACE OOS만 대상으로 하며, LOT 경계를 넘어 연속성을 계산한다.',
    },
    { doc: 'TROUBLE_FDC_FaultGuide', model: 'COMMON', section: '2. 이상 확인 순서' },
    { doc: 'TROUBLE_FDC_FaultGuide', model: 'COMMON', section: '5. 업무 조치 기준' },
  ],
  'R03가 발생하면 왜 EQP_HOLD 승인이 필요한가요': [
    {
      doc: 'TROUBLE_FDC_FaultGuide',
      model: 'COMMON',
      section: '5. 업무 조치 기준',
      excerpt:
        'strict R03가 존재하면 설비 투입 보류를 검토하며 사람의 승인이 필요하다. 승인 전에는 Kafka MES Mock 실행을 요청하지 않는다.',
    },
    { doc: 'TROUBLE_FDC_FaultGuide', model: 'COMMON', section: '6. 점검 권고에 포함할 내용' },
    { doc: 'TROUBLE_FDC_FaultGuide', model: 'COMMON', section: '1.2 R03 — 반복 OOS' },
    { doc: 'TROUBLE_FDC_FaultGuide', model: 'COMMON', section: '2. 이상 확인 순서' },
  ],
  'ETCH CD 이상일 때 PHOTO 영향은 어떻게 확인하나요': [
    {
      doc: 'TROUBLE_FDC_FaultGuide',
      model: 'COMMON',
      section: '4. 상류 공정 영향 확인',
      excerpt:
        'ETCH 단계의 CD 이상이 확인되어도 ETCH 설비를 곧바로 원인으로 단정하지 않고 PHOTO 처리 이력과 파라미터 이상 징후를 함께 확인한다.',
    },
    {
      doc: 'SPEC_PH-9000_PhotoScanner',
      model: 'PH-9000',
      section: '6. 하류 영향 확인',
      excerpt:
        'PHOTO와 ETCH의 처리 이력을 함께 확인하면 상류 공정의 영향 가능성과 ETCH 장비 자체의 이상 가능성을 구분하는 데 도움이 된다.',
    },
    {
      doc: 'SPEC_ET-7500_DryEtcher',
      model: 'ET-7500',
      section: '6. 상류 공정 영향 확인',
      excerpt:
        '문제가 발생한 웨이퍼의 PHOTO·ETCH 처리 이력과 각 단계의 측정값을 함께 비교해 점검 범위를 정한다.',
    },
    { doc: 'SPEC_ET-7500_DryEtcher', model: 'ET-7500', section: '5. 계측 항목' },
  ],
  'PH-9000에서 포커스 이탈이 있으면 무엇을 먼저 점검하나요': [
    {
      doc: 'SPEC_PH-9000_PhotoScanner',
      model: 'PH-9000',
      section: '4.2 Focus Offset (PH_FOCUS)',
      excerpt:
        'PH_FOCUS 이상은 FOC 후보로 우선 검토하고, 웨이퍼 척 표면 이물, 웨이퍼 평탄도, 포커스 센서 교정 상태를 확인한다.',
    },
    {
      doc: 'TROUBLE_FDC_FaultGuide',
      model: 'COMMON',
      section: '3.1 FOC — Focus Excursion (포커스 이탈)',
      excerpt:
        'PH_FOCUS 이상은 FOC 후보로 우선 검토한다. 초점이 벗어나면 패턴 경계가 흐려지고 CD가 작아질 수 있다.',
    },
    { doc: 'SPEC_PH-9000_PhotoScanner', model: 'PH-9000', section: '7. 소모품 및 정기 점검' },
    { doc: 'SPEC_PH-9000_PhotoScanner', model: 'PH-9000', section: '8. 챔버 비교 확인' },
  ],
  'PH-9000 노광량과 현상 온도 기준 범위는 어떻게 되나요': [
    {
      doc: 'SPEC_PH-9000_PhotoScanner',
      model: 'PH-9000',
      section: '3. 파라미터 운전 기준',
      excerpt:
        'PH_DOSE 목표값은 25.0 mJ/cm2, 관리 범위는 24.4 ~ 25.6이다. PH_DEV 목표값은 23.0 degC, 관리 범위는 22.64 ~ 23.36이다.',
    },
    { doc: 'SPEC_PH-9000_PhotoScanner', model: 'PH-9000', section: '4.1 Exposure Dose (PH_DOSE)' },
    { doc: 'SPEC_PH-9000_PhotoScanner', model: 'PH-9000', section: '4.4 Developer Temperature (PH_DEV)' },
    { doc: 'SPEC_PH-9000_PhotoScanner', model: 'PH-9000', section: '2. RECIPE 구성' },
  ],
  'PH-9000 한 챔버에 이상이 집중되면 어디를 봐야 하나요': [
    {
      doc: 'SPEC_PH-9000_PhotoScanner',
      model: 'PH-9000',
      section: '8. 챔버 비교 확인',
      excerpt:
        '한 챔버에 이상이 집중되면 해당 챔버의 척, 센서, 온도 제어를 우선 확인한다. 두 챔버에서 함께 발생하면 광원과 공통 제어 조건을 함께 확인한다.',
    },
    { doc: 'SPEC_PH-9000_PhotoScanner', model: 'PH-9000', section: '7. 소모품 및 정기 점검' },
    { doc: 'TROUBLE_FDC_FaultGuide', model: 'COMMON', section: '2. 이상 확인 순서' },
    { doc: 'SPEC_PH-9000_PhotoScanner', model: 'PH-9000', section: '1. 장비 개요' },
  ],
  'ET-7500에서 반사파가 올라가면 무엇을 점검하나요': [
    {
      doc: 'TROUBLE_FDC_FaultGuide',
      model: 'COMMON',
      section: '3.2 RFM — RF Mismatch (RF 정합 불량)',
      excerpt:
        'ET_REFL 이상은 RFM 후보로 우선 검토한다. 반사파가 증가하면 플라즈마에 전달되는 실효 전력이 줄어 식각 부족으로 이어질 수 있다.',
    },
    {
      doc: 'SPEC_ET-7500_DryEtcher',
      model: 'ET-7500',
      section: '4.2 Reflected Power (ET_REFL)',
      excerpt:
        'ET_REFL 이상은 정합기 튜닝 이탈, 챔버 내벽 폴리머 누적, 상부 전극 소모를 점검한다.',
    },
    { doc: 'SPEC_ET-7500_DryEtcher', model: 'ET-7500', section: '3. 파라미터 운전 기준' },
    { doc: 'TROUBLE_FDC_FaultGuide', model: 'COMMON', section: '7. 진단 요약표' },
  ],
  'ET-7500 CF4 유량 이상이면 어떤 원인을 확인하나요': [
    {
      doc: 'TROUBLE_FDC_FaultGuide',
      model: 'COMMON',
      section: '3.3 MFD — MFC Flow Drift (가스 유량 이탈)',
      excerpt:
        'ET_CF4 이상은 MFD 후보로 우선 검토한다. CF4 유량이 부족하거나 과다하면 식각 반응 조건이 바뀔 수 있다.',
    },
    {
      doc: 'SPEC_ET-7500_DryEtcher',
      model: 'ET-7500',
      section: '4.3 CF4 Flow (ET_CF4)',
      excerpt:
        'ET_CF4 이상은 MFC 교정 상태, 가스 배관 미세 누설, 가스 공급 압력을 확인한다.',
    },
    { doc: 'SPEC_ET-7500_DryEtcher', model: 'ET-7500', section: '7. 소모품 및 정기 점검' },
    { doc: 'TROUBLE_FDC_FaultGuide', model: 'COMMON', section: '7. 진단 요약표' },
  ],
  'ET-7500 정전척 온도 이탈은 무엇을 봐야 하나요': [
    {
      doc: 'TROUBLE_FDC_FaultGuide',
      model: 'COMMON',
      section: '3.4 TMD — ESC Temperature Deviation (정전척 온도 이상)',
      excerpt:
        'ET_ESC 이상은 TMD 후보로 우선 검토한다. 정전척 온도 이탈은 웨이퍼 위치별 식각 속도 차이를 만들 수 있다.',
    },
    {
      doc: 'SPEC_ET-7500_DryEtcher',
      model: 'ET-7500',
      section: '4.4 ESC Temperature (ET_ESC)',
      excerpt:
        'ET_ESC 이상은 He 가스 누설, 냉각수 유량, 정전척 표면 상태를 확인한다.',
    },
    { doc: 'SPEC_ET-7500_DryEtcher', model: 'ET-7500', section: '7. 소모품 및 정기 점검' },
    { doc: 'TROUBLE_FDC_FaultGuide', model: 'COMMON', section: '7. 진단 요약표' },
  ],
  'ET-7500 챔버 압력 운전 기준은 어떻게 되나요': [
    {
      doc: 'SPEC_ET-7500_DryEtcher',
      model: 'ET-7500',
      section: '3. 파라미터 운전 기준',
      excerpt:
        'ET_PRES 목표값은 25.0 mTorr, 관리 범위는 23.2 ~ 26.8, 허용 범위는 22.0 ~ 28.0이다.',
    },
    { doc: 'SPEC_ET-7500_DryEtcher', model: 'ET-7500', section: '4.1 Chamber Pressure (ET_PRES)' },
    { doc: 'SPEC_ET-7500_DryEtcher', model: 'ET-7500', section: '2. RECIPE 구성' },
    { doc: 'SPEC_ET-7500_DryEtcher', model: 'ET-7500', section: '8. 챔버 비교 확인' },
  ],
}
