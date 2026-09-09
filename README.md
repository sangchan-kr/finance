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
