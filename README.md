# KIS Intraday Trader

한국투자증권 KIS Open API를 사용하는 국내주식 장중 전략의 안전 우선 기반
프로젝트입니다. 현재 버전은 **단계 A: 시세 수집 전용**입니다. 주문을 전송하는
코드는 포함하지 않으며, 기본 설정에서도 주문이 이중으로 잠겨 있습니다.

## 현재 범위

- YAML 설정 로더와 환경변수 기반 비밀정보 주입
- SQLite 원장 및 2026-09-09 초기 모의거래 데이터
- 종목별, 동일가중, 복리 누적 수익률 계산
- OAuth 토큰과 현재가, 당일분봉, 일별분봉, 휴장일 KIS 조회 어댑터
- 주문금액, 일일한도, 손실한도, 스프레드, 중복주문, 긴급정지 위험검사
- 실전 계좌의 별도 잠금 및 계좌 allowlist 검사

## 설치

Python 3.11 이상과 `uv`가 필요합니다.

```powershell
cd E:\Work_new\CodexWorkspace\kis-intraday-trader
uv sync --extra dev
Copy-Item config\settings.example.yaml config\settings.yaml
```

KIS Developers에서 발급한 키는 파일에 쓰지 말고 현재 PowerShell 세션의
환경변수에만 설정합니다. 화면 공유, 셸 기록, 로그 노출에 주의하십시오.

GUI에서 발급한 접근 토큰도 만료시각과 함께 Windows 자격 증명 저장소에
보관하여 재시작 때 불필요하게 재발급하지 않습니다. 시세 API는 호출 간격을
제어하고 일시적인 서버 오류에 한해 제한적으로 재시도합니다.

```powershell
$env:KIS_APP_KEY = "발급받은 App Key"
$env:KIS_APP_SECRET = "발급받은 App Secret"
$env:KIS_ACCOUNT_NO = "계좌 앞 8자리"
```

모의 시세 서버를 사용하려면 `market_data_environment: demo`, 실전 시세를
사용하려면 `real`로 둡니다. 시세 환경과 계좌/주문 환경은 별도 설정입니다.

## 실행

데스크톱 프로그램을 실행합니다.

```powershell
uv run kis-trader-gui
```

콘솔 창을 남기지 않고 독립 실행하려면 `start_gui.ps1`을 실행합니다. 이 런처는
프로젝트 가상환경의 `pythonw`로 GUI를 시작한 뒤 PowerShell을 즉시 종료합니다.

`config/settings.yaml`이 없으면 예제 설정을 바탕으로 자동 생성됩니다. `API 연결`
탭에서 App Key, App Secret, 계좌번호와 실전/모의 시세 환경을 지정한 뒤 연결을
확인합니다. `종목 선택` 탭에서 공식 KOSPI/KOSDAQ 종목 마스터를 갱신하고
종목명이나 6자리 코드로 검색해 수집 목록에 추가할 수 있습니다. 수집 종목은
기본 최대 40개이며 실제 매매 대상 최대 3개 제한과 별도로 관리됩니다.

선택 화면에는 현재 환경의 호출 간격을 반영한 1회 예상 수집시간이 표시됩니다.
실전 시세는 요청당 최소 0.25초, 모의 시세는 공식 초당 1건 제한에 맞춰 최소
1.05초 간격을 적용합니다. 선택 목록과 수집 주기는 수집 시작 시 공개 설정
파일에 저장되며 API 키는 포함되지 않습니다.

대시보드는 HTS형 분할 화면으로 구성됩니다. 왼쪽 관심종목 표에서 종목명·코드·
현재가·등락률·거래량을 확인하고, 행을 선택하면 오른쪽에 현재가 요약과 KIS
일봉 히스토리가 표시됩니다. 차트는 1개월·3개월·1년 전환, 종가, 5일·20일
이동평균 및 거래량을 제공합니다. 상승은 빨강, 하락은 파랑으로 표시합니다.

창의 닫기 버튼을 누르면 시스템 트레이로 숨겨지며 수집은 계속됩니다. 트레이
아이콘의 `종료`를 선택해야 프로세스가 완전히 끝납니다. 이 기능은 로그인한
Windows 사용자 세션에서 실행되는 백그라운드 모드이며 Windows 서비스는 아닙니다.
시작부터 트레이에 두려면 `uv run kis-trader-gui --minimized`를 사용합니다.

원장을 생성하고 초기 검증 데이터를 넣습니다.

```powershell
uv run kis-trader --config config/settings.yaml init-db
```

인증과 현재가 조회를 확인합니다.

```powershell
uv run kis-trader --config config/settings.yaml price 005930
```

테스트와 정적 검사를 실행합니다.

```powershell
uv run pytest
uv run ruff check .
```

## 안전 모델

`mode: market-data-only`에서는 `order_enabled: true` 설정 자체가 로딩 단계에서
거부됩니다. 향후 주문 기능을 추가하더라도 실전 주문은 다음 조건을 모두
통과해야 합니다.

1. `mode: trading`
2. `account_environment: live`
3. `order_enabled: true`
4. 실전 계좌번호가 `live_trading.account_allowlist`에 포함됨
5. `LIVE_TRADING_UNLOCK` 값이 별도로 보관한 기대값과 일치함
6. 주문별, 일별 투자금과 손실 한도가 0보다 큰 값으로 설정됨
7. 프로젝트 루트에 `STOP_TRADING` 파일이 없음

현재 버전에는 위 잠금 검증만 있으며 **주문 전송 메서드는 없습니다**.

## 로컬 가상매매 에이전트

`simulation.enabled: true`이면 백그라운드 수집기가 KIS 당일분봉과 1호가를 읽어
ORB 및 VWAP 눌림목 전략에 동일한 `CompletedBar` 이벤트를 전달합니다. 각 전략은
별도의 시작 현금, 현금잔고, 포지션, 신호, 주문, 체결 및 자산곡선을 사용합니다.
전략은 신호만 생성하며 KIS 주문 API는 호출하지 않습니다.

공통 판단·체결 순서는 다음과 같습니다.

1. 현재 진행 중인 1분봉은 제외하고 종료시각이 확인된 완성봉만 입력합니다.
2. 이벤트 ID가 이미 처리됐다면 재접속·재생 이벤트로 보고 무시합니다.
3. 이전 이벤트에서 생성된 대기 주문을 현재 이벤트로 먼저 가상 체결합니다.
4. 데이터 지연이 `max_data_delay_seconds`를 넘으면 체결과 신규 신호를 중단합니다.
5. 두 전략이 현재 완성봉을 평가하고 신호만 원장에 기록합니다.
6. 해당 신호는 반드시 다음 이벤트 이후에만 체결 대상이 됩니다.

매수는 1호 매도호가, 매도는 1호 매수호가를 사용합니다. 과거 재생처럼 호가가
없으면 봉 종가에 `fallback_spread_bps / 2`를 불리하게 적용합니다. 이벤트당
체결 가능수량은 실제 1호가 잔량을 우선하고, 없으면 `봉 거래량 ×
fill_participation_rate`로 제한합니다. 잔량이 부족하면 부분체결 상태로 남아 다음
이벤트에서 이어집니다. 매수·매도 수수료와 매도세는 각각 설정 비율로 차감됩니다.

### ORB 1.0.0

- 범위: `range_start < 봉 종료시각 <= range_end`인 완성봉의 최고가와 최저가
- 매수: 포지션이 없고 `range_end < t <= entry_end`이며
  `종가 > 범위고가 × (1 + breakout_buffer_bps / 10000)`
- 손절: `종가 / 평균매수가 - 1 <= -stop_loss_pct`
- 익절: `종가 / 평균매수가 - 1 >= take_profit_pct`
- 시간청산: 봉 종료시각이 `exit_time` 이상

### VWAP 눌림목 1.0.0

- 전형가격: `(고가 + 저가 + 종가) / 3`
- 누적 VWAP: `Σ(전형가격 × 봉거래량) / Σ봉거래량`
- 매수: `entry_start <= t <= entry_end`, 포지션 없음, 저가가
  `VWAP × (1 + pullback_tolerance_bps / 10000)` 이하에 닿고 종가가 VWAP 및
  직전 봉 종가보다 높으며 고가가 `VWAP × (1 + min_trend_bps / 10000)` 이상
- 손절·익절·시간청산: ORB와 동일한 수익률 정의에 각 전략 설정값 적용

과거 1분봉 CSV는 라이브와 동일한 전략 이벤트 루프로 재생합니다.

```powershell
uv run kis-trader --config config/settings.yaml replay data/sample_bars.csv
```

현재 완료된 KIS 분봉을 한 번 처리하려면 다음 명령을 사용합니다.

```powershell
uv run kis-trader --config config/settings.yaml paper-once 005930 009150 042660
```

`strategy_id`와 `strategy_version`은 모든 가상 신호·주문·체결·자산곡선에
기록됩니다. SQLite v3는 기존 테이블을 삭제하거나 덮어쓰지 않고 새 테이블만
추가합니다. 전략별 일간 순수익률, 누적지수, 최대낙폭, 체결수와 승률 계산 및
외부에서 전달한 KOSPI 100 시계열과의 비교 차트 생성 API도 제공합니다.

KOSPI 100 비교 CSV는 `date,close` 헤더를 사용합니다.

```powershell
uv run kis-trader --config config/settings.yaml report `
  --kospi100-csv data/kospi100.csv `
  --output data/exports/strategy_comparison.png
```

실전 전환 시에는 전략 코드를 주문 API와 직접 연결하지 않습니다. 동일한 신호
계약 뒤에 별도 실전 브로커 어댑터를 추가하고, 기존 위험검사·계좌 allowlist·
실전 잠금·사용자 승인·주문 후 체결대사를 모두 통과시키는 방식으로 확장합니다.

## 데이터 원칙

SQLite가 원본 원장입니다. 가격과 수익률은 이진 부동소수점 오차를 피하도록
문자열 또는 `Decimal`로 저장/계산합니다. 주문 접수와 체결은 별도 테이블이며,
주문 접수를 체결로 간주하지 않습니다. 로그를 추가할 때에는 인증 헤더, App
Key, App Secret, 계좌번호를 반드시 제거해야 합니다.

첫 데이터의 `verification_status`는 `manual-unverified`입니다. 인수인계 문서의
수치를 그대로 보존한 것이므로 KIS 원천 데이터와 대사한 뒤 상태를 변경해야
합니다.

## 다음 단계

실제 키로 5거래일 동안 현재가, 호가, 09:10/15:30 분봉과 휴장일을 누락 없이
수집하는 작업이 먼저입니다. 그 후 체결/잔고 조회와 대사 로직을 만들고,
모의주문 전송 기능은 별도 승인과 테스트를 거쳐 추가합니다.
