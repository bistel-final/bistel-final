const perfectClassMetric = (support) => ({
  support,
  true_positive: support,
  false_positive: 0,
  false_negative: 0,
  precision: 1,
  recall: 1,
  f1: 1,
})

const goldenPhase = (phase) => ({
  phase,
  status: 'PASS',
  reasons: [],
  metrics: { checked: 1 },
})

// Backend의 V5-C-6.2·Golden-flow projection 회귀 fixture와 같은 공개 집계값이다.
// 개별 incident 정답, artifact 경로, provenance hash는 의도적으로 포함하지 않는다.
export const AGENT_EVALUATIONS = Object.freeze({
  fault_5class: {
    versions: {
      dataset_epoch: 'fdc_final_20260818',
      model_version: 'model-v1',
      prompt_version: 'prompt-v1',
      policy_version: 'policy-v1',
    },
    structured_prediction: { numerator: 12, denominator: 12, rate: 1 },
    evidence_valid_run: { numerator: 12, denominator: 12, rate: 1 },
    rule_action_agreement: { numerator: 12, denominator: 12, rate: 1 },
    classification: {
      population_count: 7,
      accuracy: { numerator: 7, denominator: 7, rate: 1 },
      unclassified_count: 0,
      macro_f1_5class: 1,
      observed_class_macro_f1: 1,
      by_class: {
        FOC: perfectClassMetric(2),
        RFM: perfectClassMetric(1),
        MFD: perfectClassMetric(1),
        TMD: perfectClassMetric(1),
        OTH: perfectClassMetric(2),
      },
    },
    exclusions: [
      {
        reason: 'NO_INJECTED_FAULT',
        count: 5,
        meaning: '단일 non-NRM 합성 고장 라벨이 없어 5-class 분모에서 제외',
      },
      {
        reason: 'AMBIGUOUS_LABEL',
        count: 0,
        meaning: '서로 다른 non-NRM 합성 라벨이 둘 이상이라 분모에서 제외',
      },
    ],
    metrology_observed_count: 48,
    metrology_total_lot_hist_count: 600,
    hard_gate_passed: true,
    hard_gate_reasons: [],
    public_fault_ground_truth_available: true,
    production_ground_truth_available: false,
    label_source: 'SYNTHETIC_GENERATOR',
    usage_scope: 'EVALUATION_ONLY',
    production_performance_disclaimer:
      '이 결과는 Generator 공개 합성 라벨 benchmark이며 실제 생산 공정 성능을 나타내지 않는다. 분류 모집단은 7건이고 클래스별 support는 1~2건이므로 개별 클래스 지표를 성능 추정치로 해석하지 않는다.',
  },
  golden_flow: {
    dataset_epoch: 'fdc_final_20260818',
    status: 'PASS',
    phases: [
      'PREFLIGHT',
      'BATCH_BASELINE',
      'PRE_APPROVAL',
      'DECISIONS',
      'UNKNOWN',
      'MANUAL_RETRY',
      'SECOND_BATCH',
    ].map(goldenPhase),
  },
  fault_5class_empty_reason: null,
  golden_flow_empty_reason: null,
})
