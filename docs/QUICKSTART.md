# Qlib 퀵스타트 가이드

> Docker 기반 로컬 개발 환경 셋업부터 모델 학습까지의 실전 가이드

---

## 목차

1. [환경 셋업](#1-환경-셋업)
2. [컨테이너 명령어 레퍼런스](#2-컨테이너-명령어-레퍼런스)
3. [개발 워크플로우](#3-개발-워크플로우)
4. [학습 단계별 실행](#4-학습-단계별-실행)
5. [볼륨 & 데이터 영속성](#5-볼륨--데이터-영속성)
6. [트러블슈팅](#6-트러블슈팅)

---

## 1. 환경 셋업

### 사전 요구사항

- Docker Desktop 설치 및 실행
- Git으로 qlib 레포지토리 클론

### 최초 셋업 (한 번만)

```bash
chmod +x start-local.sh

# 이미지 빌드 + US 마켓 데이터 다운로드 (~200MB) + 초기화 검증
./start-local.sh setup
```

`setup`이 수행하는 작업:
1. Docker 이미지 빌드 (Python 3.11 + Cython 확장 컴파일 + 의존성 설치)
2. US 마켓 데이터 다운로드 → Docker named volume `qlib_data`에 저장
3. `qlib.auto_init()` 검증

### 추가 데이터 다운로드 (선택)

```bash
./start-local.sh data cn      # 중국 A주 데이터
./start-local.sh data both    # US + CN 모두
```

---

## 2. 컨테이너 명령어 레퍼런스

### 시작 / 종료

| 명령 | 설명 |
|------|------|
| `./start-local.sh up` | 백그라운드 컨테이너 시작 (persistent, 추천) |
| `./start-local.sh` | 일회성 인터랙티브 bash (종료 시 컨테이너 삭제) |
| `./start-local.sh stop` | 컨테이너 종료 및 정리 |
| `./start-local.sh status` | 실행 중인 컨테이너 상태 확인 |

### 컨테이너 접근

| 명령 | 설명 |
|------|------|
| `./start-local.sh shell` | 실행 중인 컨테이너에 bash 접속 |
| `./start-local.sh exec <cmd>` | 외부에서 컨테이너에 명령 실행 |

### 작업 도구

| 명령 | 설명 |
|------|------|
| `./start-local.sh jupyter` | Jupyter Notebook 서버 시작 (http://localhost:8888) |
| `./start-local.sh run <yaml>` | qlib 워크플로우 YAML 실행 (qrun) |
| `./start-local.sh test` | pytest 실행 (slow 테스트 제외) |

---

## 3. 개발 워크플로우

### 추천: IDE + 백그라운드 컨테이너

이 방식이 가장 생산적입니다. IDE에서 편집한 파일이 즉시 컨테이너에 반영됩니다.

```bash
# 1. 백그라운드 컨테이너 시작
./start-local.sh up

# 2. IDE에서 workspace/my_strategy.py 작성/편집

# 3. 외부에서 실행
./start-local.sh exec python workspace/my_strategy.py

# 4. 필요 시 컨테이너 내부 진입
./start-local.sh shell

# 5. 작업 완료 후 종료
./start-local.sh stop
```

### 일회성 실행

간단한 테스트나 일회성 작업에 적합합니다.

```bash
# 인터랙티브 bash 진입 (exit 시 컨테이너 자동 삭제)
./start-local.sh

# 컨테이너 내부에서 직접 실행
python workspace/step1_init.py
```

### 작업 디렉토리

| 경로 | 용도 |
|------|------|
| `workspace/` | 개인 스크립트/노트북 (`.gitignore` 처리됨) |
| `examples/` | qlib 공식 예제 |
| `examples/benchmarks/` | 벤치마크 모델 YAML 설정 |

---

## 4. 학습 단계별 실행

`workspace/` 디렉토리에 학습용 스크립트가 준비되어 있습니다.
컨테이너 내부 또는 `exec`으로 순서대로 실행합니다.

### Step 1: 초기화 확인

```bash
python workspace/step1_init.py
```

`qlib.auto_init()`으로 `config.yaml`을 자동 로드합니다.
Region, Provider URI 등 설정이 정상 출력되면 성공.

**핵심 코드:**
```python
import qlib
qlib.auto_init()           # config.yaml 자동 탐색 → 초기화
from qlib.config import C  # 전역 설정 싱글턴 접근
```

### Step 2: 데이터 탐색

```bash
python workspace/step2_data_explore.py
```

`qlib.data.D` 객체로 데이터에 접근합니다:
- **거래일 캘린더**: `D.calendar(start_time, end_time)`
- **종목 리스트**: `D.instruments("all")` → `D.list_instruments()`
- **피처 데이터**: `D.features(instruments, fields, start_time, end_time)`

**핵심 코드:**
```python
from qlib.data import D

cal = D.calendar(start_time="2020-01-01", end_time="2020-01-31")
instruments = D.instruments("all")
stock_list = D.list_instruments(instruments=instruments, start_time="2020-01-01", end_time="2020-12-31")
df = D.features(instruments=["AAPL"], fields=["$close", "$volume"], start_time="2020-01-01", end_time="2020-01-31")
```

### Step 3: 피처 엔지니어링

```bash
python workspace/step3_features.py
```

qlib의 수식 기반 피처 엔지니어링 문법:

| 수식 | 의미 |
|------|------|
| `$close` | 종가 원시 데이터 |
| `Ref($close, 1)` | 전일 종가 |
| `$close/Ref($close,1)-1` | 일간 수익률 |
| `Mean($close, 5)` | 5일 이동평균 |
| `Std($close, 20)` | 20일 변동성 (표준편차) |
| `Max($high, 10)` | 10일 최고가 |
| `Min($low, 10)` | 10일 최저가 |

**핵심 코드:**
```python
features = D.features(
    instruments=["AAPL"],
    fields=["$close", "Ref($close, 1)", "$close/Ref($close,1)-1", "Mean($close, 5)"],
    start_time="2020-01-01",
    end_time="2020-03-31",
)
```

### Step 4: 데이터셋 & Alpha158 핸들러

```bash
python workspace/step4_dataset.py
```

Alpha158 핸들러가 158개 팩터를 자동 생성하고, train/valid/test로 분할합니다.

- **Alpha158**: K봉 비율, 가격 변동, 롤링 통계 등 158개 팩터
- **Alpha360**: 60일간 OHLCV 6개 피처 원시값 (6 x 60 = 360개)

**핵심 코드:**
```python
from qlib.data.dataset import DatasetH

dataset = DatasetH(
    handler={
        "class": "Alpha158",
        "module_path": "qlib.contrib.data.handler",
        "kwargs": {
            "instruments": "all",
            "start_time": "2015-01-01",
            "end_time": "2020-12-31",
            "fit_start_time": "2015-01-01",
            "fit_end_time": "2018-12-31",
        },
    },
    segments={
        "train": ("2015-01-01", "2018-12-31"),
        "valid": ("2019-01-01", "2019-12-31"),
        "test": ("2020-01-01", "2020-12-31"),
    },
)

train_data = dataset.prepare("train")
```

### Step 5: 모델 학습 (YAML 워크플로우)

```bash
# Linear 모델 (가장 빠름)
qrun examples/benchmarks/Linear/workflow_config_linear_Alpha158.yaml

# LightGBM 모델
qrun examples/benchmarks/LightGBM/workflow_config_lightgbm_Alpha158.yaml
```

> **주의**: 기본 YAML은 CN 마켓 기준입니다. US 마켓용으로 사용하려면
> `market`, `benchmark`, `provider_uri`, `region` 값을 수정해야 합니다.

### Step 6: Python 코드 워크플로우

`examples/workflow_by_code.py`를 참고하여 Python 코드로 전체 파이프라인을 실행할 수 있습니다:

```python
model.fit(dataset)                    # 학습
predictions = model.predict(dataset)  # 예측
# → TopkDropoutStrategy → backtest_daily → 성과 분석
```

### Step 7: 결과 분석 (MLflow)

```bash
# 컨테이너 내부에서
mlflow ui --host 0.0.0.0 --port 5000
# → 브라우저에서 http://localhost:5050 접속
```

실험 결과는 `/app/mlruns/` (호스트: `./mlruns/`)에 저장됩니다.

---

## 5. 볼륨 & 데이터 영속성

`docker-compose.dev.yml`에 설정된 볼륨:

| 볼륨 | 컨테이너 경로 | 유형 | 용도 |
|------|-------------|------|------|
| `.:/app` | `/app` | Bind mount | 소스코드 양방향 동기화 |
| `qlib_data` | `/root/.qlib` | Named volume | 마켓 데이터 (영속) |
| `mlruns` | `/app/mlruns` | Named volume | MLflow 실험 결과 (영속) |
| `pip_cache` | `/root/.cache/pip` | Named volume | pip 다운로드 캐시 (영속) |

### 영속성 정리

| 항목 | 컨테이너 종료 후 | 비고 |
|------|----------------|------|
| 소스 코드 & workspace 파일 | **유지** | Bind mount (`.:/app`) |
| 마켓 데이터 | **유지** | Named volume `qlib_data` |
| MLflow 결과 | **유지** | Named volume `mlruns` |
| pip 추가 설치 패키지 | 유실 (캐시는 유지) | 재설치 시 캐시로 빠름 |
| 컨테이너 내 `/root/` 등 기타 경로 | 유실 | 필요 시 볼륨 추가 |

### 포트 매핑

| 호스트 포트 | 컨테이너 포트 | 서비스 |
|------------|-------------|--------|
| 8888 | 8888 | Jupyter Notebook |
| 5050 | 5000 | MLflow UI |

---

## 6. 트러블슈팅

### 컨테이너가 시작되지 않을 때

```bash
# Docker 상태 확인
docker ps -a
./start-local.sh status

# 로그 확인
docker compose -f docker-compose.dev.yml -p qlib logs
```

### `shell` / `exec` 실행 시 "no container found"

`up`으로 백그라운드 컨테이너를 먼저 시작해야 합니다:

```bash
./start-local.sh up       # 먼저 시작
./start-local.sh shell    # 그 다음 접속
```

### 포트 충돌

8888 또는 5000 포트가 이미 사용 중이면 `docker-compose.dev.yml`에서 호스트 포트를 변경합니다:

```yaml
ports:
  - "8889:8888"   # 호스트 8889 → 컨테이너 8888
  - "5001:5000"
```

### Cython 확장 컴파일 에러

`docker-entrypoint.sh`가 컨테이너 시작 시 `.so` 파일이 없으면 자동 컴파일합니다.
수동 컴파일이 필요한 경우:

```bash
./start-local.sh exec make prerequisite
```

### 데이터 볼륨 초기화 (재다운로드)

```bash
./start-local.sh stop
docker volume rm qlib_qlib_data
./start-local.sh setup
```
