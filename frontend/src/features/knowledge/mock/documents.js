// dc.html 문서 검색 mock — 예시 질문 3건, top_k=4 결과 카드
export const DOC_FILTERS = ['전체', 'PH-9000', 'ET-7500']

export const DOC_CHIPS = [
  '반사파가 올라가면 무슨 문제인가',
  '포커스가 벗어나면 CD가 어떻게 되나',
  '장비를 세우려면 승인이 필요한가',
]

export const DOC_SCORES = [0.88, 0.83, 0.79, 0.74]

export const DOC_DB = {
  '반사파가 올라가면 무슨 문제인가': [
    {
      doc: 'TROUBLE_FDC_FaultGuide',
      model: 'COMMON',
      section: '3.2 RFM — RF Mismatch (RF 정합 불량)',
      excerpt:
        'Reflected Power가 상승한다. Source RF Power 설정값은 그대로인데 반사파만 올라간다. 반사파가 커지면 플라즈마에 실제로 들어가는 실효 전력이 줄어든다.',
    },
    { doc: 'SPEC_ET-7500_DryEtcher', model: 'ET-7500', section: '4.2 Reflected Power (ET_REFL)' },
    { doc: 'SPEC_ET-7500_DryEtcher', model: 'ET-7500', section: '3. 센서 운전 기준' },
    { doc: 'TROUBLE_FDC_FaultGuide', model: 'COMMON', section: '7. 진단 요약표' },
  ],
  '포커스가 벗어나면 CD가 어떻게 되나': [
    {
      doc: 'TROUBLE_FDC_FaultGuide',
      model: 'COMMON',
      section: '3.1 FOC — Focus Excursion (포커스 이탈)',
      excerpt:
        '발생 장비 PH-9000 (PHOTO), 주 센서 PH_FOCUS, 주 RECIPE STEP EXPOSE. 관리한계 ±36 nm · 규격한계 ±60 nm',
    },
    { doc: 'SPEC_PH-9000_PhotoScanner', model: 'PH-9000', section: '4.2 Focus Offset (PH_FOCUS)' },
    { doc: 'SPEC_PH-9000_PhotoScanner', model: 'PH-9000', section: '6. 하류 영향' },
    { doc: 'SPEC_ET-7500_DryEtcher', model: 'ET-7500', section: '6.1 판별 절차' },
  ],
  '장비를 세우려면 승인이 필요한가': [
    {
      doc: 'TROUBLE_FDC_FaultGuide',
      model: 'COMMON',
      section: '5.1 왜 EQP_HOLD만 승인이 필요한가',
      excerpt:
        'EQP_HOLD는 장비를 세워 후속 LOT 투입을 전면 차단한다. 생산 손실이 크므로 사람이 최종 판단해야 한다. 실제 fab에서도 설비 홀드는 담당자 승인 사항이다.',
    },
    { doc: 'TROUBLE_FDC_FaultGuide', model: 'COMMON', section: '5. 조치 결정 기준' },
    { doc: 'TROUBLE_FDC_FaultGuide', model: 'COMMON', section: '5.2 조치 상향 조건' },
    { doc: 'SPEC_ET-7500_DryEtcher', model: 'ET-7500', section: '6.2 상류 원인일 때의 조치' },
  ],
}
