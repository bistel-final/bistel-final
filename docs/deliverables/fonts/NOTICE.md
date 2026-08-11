# 번들 글꼴 라이선스

제출용 PDF 렌더링에 쓰는 글꼴이다. 둘 다 SIL Open Font License 1.1 — 재배포·임베드 허용.

| 파일 | 원본 | 라이선스 |
|---|---|---|
| `Pretendard-Regular.ttf`, `Pretendard-Bold.ttf` | [Pretendard](https://github.com/orioncactus/Pretendard) (길형진) — `PretendardVariable.ttf`를 fontTools `varLib.instancer`로 각 weight 정적 인스턴스화 | SIL OFL 1.1 |
| `D2Coding-Bold.ttf` | [D2Coding](https://github.com/naver/d2codingfont) (네이버) | SIL OFL 1.1 |

reportlab의 `TTFont`는 glyf 기반 TrueType만 읽는다. Pretendard 배포본은 CFF 기반 OTF라 그대로 못 쓰고, Variable TTF(glyf)를 정적 인스턴스로 변환해 등록했다.

인스턴스 재생성:

```bash
pip install fonttools
fonttools varLib.instancer PretendardVariable.ttf wght=400 -o Pretendard-Regular.ttf
fonttools varLib.instancer PretendardVariable.ttf wght=700 -o Pretendard-Bold.ttf
```
