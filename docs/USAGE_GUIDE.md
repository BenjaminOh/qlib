# Qlib 종합 사용 가이드

> Microsoft의 AI 기반 퀀트 투자 플랫폼 (pyqlib) — 코드베이스 기반 분석 가이드

---

## 목차

1. [개요 & 설치](#1-개요--설치)
2. [초기화 & 설정](#2-초기화--설정)
3. [데이터 파이프라인](#3-데이터-파이프라인)
4. [모델](#4-모델)
5. [전략 & 백테스팅](#5-전략--백테스팅)
6. [워크플로우 & 실험 관리](#6-워크플로우--실험-관리)
7. [평가 & 리포트](#7-평가--리포트)
8. [고급 기능](#8-고급-기능)
9. [유틸리티 스크립트](#9-유틸리티-스크립트)
10. [자주 묻는 질문 / 트러블슈팅](#10-자주-묻는-질문--트러블슈팅)

---

## 1. 개요 & 설치

### Qlib이란?

Qlib은 Microsoft에서 개발한 **AI 기반 퀀트 투자 플랫폼**이다. 데이터 수집부터 피처 엔지니어링, 모델 학습, 백테스팅, 실험 관리까지 퀀트 리서치의 전체 워크플로우를 지원한다.

핵심 특징:
- **데이터 파이프라인**: 바이너리 포맷 기반 고속 데이터 처리, 수식 기반 피처 엔지니어링
- **35+ 내장 모델**: LightGBM, XGBoost부터 Transformer, GRU, LSTM 등 딥러닝 모델까지
- **백테스팅 엔진**: 거래 비용, 호가 제한, 리전별 규칙을 반영한 현실적 시뮬레이션
- **실험 관리**: MLflow 기반 자동 실험 추적 및 비교
- **YAML 또는 Python 코드** 두 가지 인터페이스 제공

### 설치 방법

#### pip 설치 (기본)

```bash
pip install pyqlib
```

#### 소스에서 설치 (개발용)

```bash
git clone https://github.com/microsoft/qlib.git
cd qlib

# 전체 설치 (Cython 확장 컴파일 + 모든 의존성)
make dev

# 또는 최소 설치
make install

# 수동 설치:
make prerequisite          # Cython .pyx → .so 컴파일 (rolling, expanding)
pip install -e .[dev]      # 편집 가능 모드 + 개발 의존성
```

> **참고**: Cython 확장은 `qlib/data/_libs/`에 있는 `rolling.pyx`와 `expanding.pyx`이다. `make prerequisite`로 컴파일하며, `.so` 파일이 이미 있으면 건너뛴다.

#### Docker 기반 설치 (원클릭 셋업)

```bash
# 1. 원클릭 셋업: 빌드 → US 데이터 다운로드 → 검증
chmod +x start-local.sh
./start-local.sh setup

# 2. 대화형 컨테이너 시작
./start-local.sh
```

`setup` 커맨드는 다음을 자동으로 수행한다:
1. Docker 이미지 빌드
2. US 마켓 데이터 다운로드 (~200MB)
3. `config.yaml` 기반 `qlib.auto_init()` 검증

컨테이너 진입 후 별도 초기화 없이 바로 사용 가능:

```python
import qlib
qlib.auto_init()  # config.yaml 자동 로드 (US 마켓)
```

#### 데이터 다운로드 (선택)

```bash
# US 데이터 (기본값)
./start-local.sh data

# CN 데이터
./start-local.sh data cn

# US + CN 모두
./start-local.sh data both
```

### 데이터 다운로드 (로컬 설치)

로컬 환경(Docker 미사용)에서는 직접 스크립트를 실행한다:

```bash
# 미국 주식 데이터
python scripts/get_data.py qlib_data --target_dir ~/.qlib/qlib_data/us_data --region us

# 중국 A주 데이터
python scripts/get_data.py qlib_data --target_dir ~/.qlib/qlib_data/cn_data
```

---

## 2. 초기화 & 설정

### qlib.init()

Qlib 사용의 첫 단계는 초기화다. `qlib.init()`으로 데이터 경로, 리전, 캐시 등을 설정한다.

```python
import qlib
from qlib.constant import REG_US

# US 마켓 초기화
qlib.init(
    provider_uri="~/.qlib/qlib_data/us_data",
    region=REG_US,
)
```

```python
# CN 마켓 초기화
from qlib.constant import REG_CN

qlib.init(
    provider_uri="~/.qlib/qlib_data/cn_data",
    region=REG_CN,
)
```

#### 주요 파라미터

| 파라미터 | 설명 | 기본값 |
|----------|------|--------|
| `default_conf` | 모드 선택: `"client"` 또는 `"server"` | `"client"` |
| `provider_uri` | 데이터 경로 (str 또는 dict) | `"~/.qlib/qlib_data/cn_data"` |
| `region` | 시장 리전: `REG_CN`, `REG_US`, `REG_TW` | `REG_CN` |
| `expression_cache` | 수식 캐시 (`None` 또는 `"DiskExpressionCache"`) | `None` |
| `dataset_cache` | 데이터셋 캐시 (`None` 또는 `"DiskDatasetCache"`) | `None` |
| `redis_host` / `redis_port` | Redis 캐시 서버 | `"127.0.0.1"` / `6379` |
| `logging_level` | 로깅 레벨 | `logging.INFO` |
| `custom_ops` | 사용자 정의 연산자 리스트 | `[]` |

#### 멀티 주파수 데이터 설정

일봉과 분봉 데이터를 동시에 사용할 때는 `provider_uri`를 딕셔너리로 전달한다:

```python
qlib.init(
    provider_uri={
        "day": "~/.qlib/qlib_data/cn_data",
        "1min": "~/.qlib/qlib_data/cn_data_1min",
    },
    region=REG_CN,
)
```

### qlib.auto_init()

프로젝트 루트에 `config.yaml`이 있으면 자동으로 찾아서 초기화한다. Docker 환경에서는 `./start-local.sh setup`이 이미 `config.yaml`을 생성하므로 별도 설정 없이 바로 사용 가능하다:

```python
import qlib
qlib.auto_init()  # config.yaml 자동 탐색 → 초기화
```

프로젝트의 기본 `config.yaml` (US 마켓):

```yaml
provider_uri: "~/.qlib/qlib_data/us_data"
region: us
exp_manager:
    class: "MLflowExpManager"
    module_path: "qlib.workflow.expm"
    kwargs:
        uri: "file:///app/mlruns"
        default_exp_name: "Experiment"
```

> **US 마켓 참고 정보:**
> - **벤치마크**: `^GSPC` (S&P 500), `^NDX` (NASDAQ-100), `^DJI` (Dow Jones)
> - **trade_unit**: 1 (1주 단위, CN의 100주와 다름)
> - **limit_threshold**: `None` (가격 제한 없음)
> - **거래 시간**: 9:30~16:00 ET (단일 세션)

CN 마켓용 `config.yaml` 예시:

```yaml
provider_uri: "~/.qlib/qlib_data/cn_data"
region: cn
exp_manager:
    class: "MLflowExpManager"
    module_path: "qlib.workflow.expm"
    kwargs:
        uri: "file:///path/to/mlruns"
        default_exp_name: "Experiment"
```

`config.yaml` 예시 (ref 타입 — 공유 설정 참조):

```yaml
conf_type: ref
qlib_cfg: '/path/to/shared_config.yaml'
qlib_cfg_update:
    exp_manager:
        class: "MLflowExpManager"
        module_path: "qlib.workflow.expm"
        kwargs:
            uri: "file:///my/project/mlruns"
            default_exp_name: "MyExperiment"
```

### QSettings 환경변수

`QLIB_` 접두사를 사용한 환경변수로 기본값을 오버라이드할 수 있다 (pydantic-settings 기반):

```bash
export QLIB_PROVIDER_URI="~/.qlib/qlib_data/us_data"
export QLIB_MLFLOW_URI="file:///path/to/mlruns"
export QLIB_MLFLOW_DEFAULT_EXP_NAME="MyExperiment"
```

### Client vs Server 모드

| 항목 | Client 모드 | Server 모드 |
|------|------------|------------|
| 용도 | 로컬 개발, 단일 머신 | 분산 환경, NFS 마운트 |
| 캐시 | 기본 비활성화 | DiskExpressionCache, DiskDatasetCache |
| 데이터 | 로컬 파일 | NFS 마운트 또는 Redis 기반 |
| 설정 | `auto_mount=False` | Redis 연결 필요 |

### C 싱글턴 설정 구조

`qlib.config.C`는 전역 설정 싱글턴이다. 딕셔너리처럼 접근 가능하다:

```python
from qlib.config import C

# 설정 읽기
print(C["provider_uri"])
print(C.region)
print(C["redis_host"])
print(C["mem_cache_size_limit"])  # 기본값: 500
print(C["mem_cache_expire"])      # 기본값: 3600초 (1시간)
```

---

## 3. 데이터 파이프라인

### 데이터 다운로드

```bash
# 기본 일봉 데이터
python scripts/get_data.py qlib_data --target_dir ~/.qlib/qlib_data/cn_data

# 미국 데이터
python scripts/get_data.py qlib_data --target_dir ~/.qlib/qlib_data/us_data --region us
```

### Provider 패턴

Qlib은 **Provider 패턴**으로 데이터 접근을 추상화한다. 주요 Provider:

| Provider | 변수 | 역할 |
|----------|------|------|
| `CalendarProvider` | `Cal` | 거래일 캘린더 조회 |
| `InstrumentProvider` | `Inst` | 종목 리스트 조회 |
| `FeatureProvider` | `FeatureD` | 개별 피처 로드 |
| `ExpressionProvider` | — | 수식 기반 피처 계산 |
| `DatasetProvider` | — | 데이터셋 생성 |
| `PITProvider` | — | Point-in-Time 데이터 |

데이터 접근은 `D` (DataWrapper)를 통해 한다:

```python
from qlib.data import D

# 거래일 캘린더
calendar = D.calendar(start_time="2020-01-01", end_time="2020-12-31")

# 종목 리스트
instruments = D.instruments(market="csi300")
stock_list = D.list_instruments(instruments=instruments, start_time="2020-01-01", end_time="2020-12-31")

# 피처 데이터 로드
features = D.features(
    instruments=["SH600000", "SH600036"],
    fields=["$close", "$volume", "Ref($close, 1)", "$close/Ref($close, 1)-1"],
    start_time="2020-01-01",
    end_time="2020-12-31",
)
```

### 피처 엔지니어링 연산자 (ops.py)

Qlib의 강력한 기능 중 하나는 **수식 기반 피처 엔지니어링**이다. `$` 접두사로 원시 피처를 참조하고, 연산자를 조합하여 새로운 피처를 만든다.

#### 데이터 표현식 문법

```python
# 기본 피처 참조
"$close"                           # 종가
"$volume"                          # 거래량

# 수익률 계산
"$close/Ref($close,1)-1"          # 일간 수익률
"Ref($close, -2)/Ref($close, -1) - 1"  # 라벨 (미래 수익률)

# 이동평균
"Mean($close, 5)"                 # 5일 이동평균
"EMA($close, 12)"                 # 12일 지수이동평균

# 변동성
"Std($close, 20)"                 # 20일 표준편차
"Slope($close, 20)/$close"        # 선형 회귀 기울기 (정규화)
```

#### 전체 연산자 목록

**Element-wise 연산자 (단항)**

| 연산자 | 설명 | 예시 |
|--------|------|------|
| `Abs` | 절대값 | `Abs($close - $open)` |
| `Sign` | 부호 (+1, 0, -1) | `Sign($close - $open)` |
| `Log` | 자연로그 | `Log($volume)` |
| `Not` | 비트 NOT | `Not(Gt($close, $open))` |
| `Mask` | 다른 종목의 피처 참조 | `Mask($close, SH000300)` |
| `ChangeInstrument` | 계산 대상 종목 변경 | `ChangeInstrument('SH000300', $close)` |

**Pair-wise 연산자 (이항)**

| 연산자 | 설명 | 예시 |
|--------|------|------|
| `Add` / `+` | 덧셈 | `$close + $open` |
| `Sub` / `-` | 뺄셈 | `$close - $open` |
| `Mul` / `*` | 곱셈 | `$close * $volume` |
| `Div` / `/` | 나눗셈 | `$close / $open` |
| `Power` | 거듭제곱 | `Power($close, 2)` |
| `Greater` | 최대값 | `Greater($close, $open)` |
| `Less` | 최소값 | `Less($close, $open)` |
| `Gt` | 크다 (bool) | `Gt($close, $open)` |
| `Ge` | 크거나 같다 | `Ge($close, $open)` |
| `Lt` | 작다 | `Lt($close, $open)` |
| `Le` | 작거나 같다 | `Le($close, $open)` |
| `Eq` | 같다 | `Eq($close, $open)` |
| `Ne` | 다르다 | `Ne($close, $open)` |
| `And` / `&` | AND | `And(Gt($close, $open), Gt($volume, 1e6))` |
| `Or` / `\|` | OR | `Or(Gt($close, $open), Gt($volume, 1e6))` |

**Triple-wise 연산자 (삼항)**

| 연산자 | 설명 | 예시 |
|--------|------|------|
| `If` | 조건부 선택 | `If(Gt($close, $open), $close, $open)` |

**Rolling 연산자 (시계열)**

| 연산자 | 설명 | 파라미터 |
|--------|------|----------|
| `Ref` | 과거/미래 참조 | `Ref($close, 1)` → 1일 전 종가 |
| `Mean` | 이동평균 (MA) | `Mean($close, 5)` |
| `Sum` | 이동합 | `Sum($volume, 10)` |
| `Std` | 이동 표준편차 | `Std($close, 20)` |
| `Var` | 이동 분산 | `Var($close, 20)` |
| `Skew` | 이동 왜도 (N≥3) | `Skew($close, 20)` |
| `Kurt` | 이동 첨도 (N≥4) | `Kurt($close, 20)` |
| `Max` | 이동 최대값 | `Max($high, 20)` |
| `Min` | 이동 최소값 | `Min($low, 20)` |
| `IdxMax` | 최대값 위치 | `IdxMax($close, 20)` |
| `IdxMin` | 최소값 위치 | `IdxMin($close, 20)` |
| `Med` | 이동 중앙값 | `Med($close, 20)` |
| `Mad` | 이동 평균 절대 편차 | `Mad($close, 20)` |
| `Rank` | 이동 백분위 | `Rank($close, 20)` |
| `Quantile` | 이동 분위수 | `Quantile($close, 20, 0.75)` |
| `Count` | 이동 비결측값 수 | `Count($volume, 20)` |
| `Delta` | 이동 변화량 | `Delta($close, 5)` |
| `Slope` | 선형 회귀 기울기 | `Slope($close, 20)` |
| `Rsquare` | 선형 회귀 R² | `Rsquare($close, 20)` |
| `Resi` | 선형 회귀 잔차 | `Resi($close, 20)` |
| `WMA` | 가중 이동평균 | `WMA($close, 10)` |
| `EMA` | 지수 이동평균 | `EMA($close, 12)` |

> **참고**: N=0이면 `expanding` (누적) 모드로 동작한다. 0 < N < 1이면 `ewm(alpha=N)` 모드로 동작한다.

**Pair Rolling 연산자**

| 연산자 | 설명 | 예시 |
|--------|------|------|
| `Corr` | 이동 상관계수 | `Corr($close, $volume, 20)` |
| `Cov` | 이동 공분산 | `Cov($close, $volume, 20)` |

**시계열 리샘플링 연산자**

| 연산자 | 설명 | 예시 |
|--------|------|------|
| `TResample` | 주파수 리샘플링 | `TResample($close, "5T", "last")` |

**PIT 연산자**

| 연산자 | 설명 |
|--------|------|
| `P` | Point-in-Time 피처 참조 |
| `PRef` | PIT 과거 참조 |

### Alpha158 / Alpha360 핸들러

Qlib은 사전 정의된 피처셋을 **데이터 핸들러**로 제공한다.

#### Alpha158

158개의 팩터를 포함하는 핸들러. K봉(OHLCV), 가격 비율, 롤링 통계 등을 조합한다.

```python
from qlib.contrib.data.handler import Alpha158

handler = Alpha158(
    instruments="csi300",
    start_time="2008-01-01",
    end_time="2020-08-01",
    fit_start_time="2008-01-01",
    fit_end_time="2014-12-31",
)
```

구성 요소:
- **kbar**: K봉 관련 피처 (OHLC 비율 등)
- **price**: 가격 변동 피처 (OPEN, HIGH, LOW, VWAP의 윈도우별 참조)
- **rolling**: 롤링 통계 (평균, 표준편차, 최대/최소 등)

기본 라벨: `Ref($close, -2)/Ref($close, -1) - 1` (이틀 후 수익률)

#### Alpha360

360개의 원시 피처를 사용하는 핸들러. 60일간의 OHLCV 6개 피처를 그대로 사용한다 (6 × 60 = 360).

```python
from qlib.contrib.data.handler import Alpha360

handler = Alpha360(
    instruments="csi500",
    start_time="2008-01-01",
    end_time="2020-08-01",
    fit_start_time="2008-01-01",
    fit_end_time="2014-12-31",
)
```

기본 전처리:
- **infer_processors**: `ProcessInf` → `ZScoreNorm` → `Fillna`
- **learn_processors**: `DropnaLabel` → `CSZScoreNorm` (라벨)

VWAP 기반 라벨 변형도 있다: `Alpha158vwap`, `Alpha360vwap`

### DatasetH 사용법

`DatasetH`는 데이터를 train/valid/test 세그먼트로 분할하여 제공한다:

```python
from qlib.data.dataset import DatasetH
from qlib.contrib.data.handler import Alpha158

handler_config = {
    "class": "Alpha158",
    "module_path": "qlib.contrib.data.handler",
    "kwargs": {
        "instruments": "csi300",
        "start_time": "2008-01-01",
        "end_time": "2020-08-01",
        "fit_start_time": "2008-01-01",
        "fit_end_time": "2014-12-31",
    },
}

dataset = DatasetH(
    handler=handler_config,
    segments={
        "train": ("2008-01-01", "2014-12-31"),
        "valid": ("2015-01-01", "2016-12-31"),
        "test": ("2017-01-01", "2020-08-01"),
    },
)

# 데이터 준비
train_data = dataset.prepare("train")
print(train_data.head())
```

### PIT (Point-in-Time) 데이터

재무 데이터처럼 발표 시점이 중요한 데이터를 정확하게 처리한다. `P`와 `PRef` 연산자로 PIT 피처를 사용할 수 있다.

```python
# PIT 데이터 덤프
python scripts/dump_pit.py dump_all --qlib_dir ~/.qlib/qlib_data/cn_data --csv_path <pit_csv_path>
```

---

## 4. 모델

### 모델 베이스 클래스

| 클래스 | 설명 |
|--------|------|
| `BaseModel` | 최상위 추상 클래스. `predict()` 메서드 필수 구현 |
| `Model` | 학습 가능 모델. `fit(dataset)` + `predict(dataset)` |
| `ModelFT` | 파인튜닝 지원 모델. `finetune(dataset)` 추가 |

```python
# 모델 사용 패턴
model.fit(dataset)                    # 학습
predictions = model.predict(dataset)  # 예측 (test 세그먼트 기본)
predictions = model.predict(dataset, segment="valid")  # 검증 세그먼트
```

### 사용 가능한 모델 전체 목록

#### GBDT 계열

| 모델 | 파일 | 프레임워크 | 설명 |
|------|------|-----------|------|
| `LGBModel` | `gbdt.py` | LightGBM | Gradient Boosting (기본 벤치마크) |
| `XGBModel` | `xgboost.py` | XGBoost | Extreme Gradient Boosting |
| `CatBoostModel` | `catboost_model.py` | CatBoost | Categorical Boosting |
| `HFLGBModel` | `highfreq_gdbt_model.py` | LightGBM | 고빈도 데이터용 GBDT |

#### 딥러닝 — RNN 계열

| 모델 | 파일 | 설명 |
|------|------|------|
| `LSTM` | `pytorch_lstm.py` | Long Short-Term Memory |
| `GRU` | `pytorch_gru.py` | Gated Recurrent Unit |
| `ALSTM` | `pytorch_alstm.py` | Attention-based LSTM |
| `KRNN` | `pytorch_krnn.py` | Knowledge-driven RNN |
| `LSTM_TS` | `pytorch_lstm_ts.py` | LSTM (시계열 데이터셋용) |
| `GRU_TS` | `pytorch_gru_ts.py` | GRU (시계열 데이터셋용) |
| `ALSTM_TS` | `pytorch_alstm_ts.py` | Attention LSTM (시계열) |

#### 딥러닝 — Transformer 계열

| 모델 | 파일 | 설명 |
|------|------|------|
| `Transformer` | `pytorch_transformer.py` | Vanilla Transformer |
| `Localformer` | `pytorch_localformer.py` | Local-aware Transformer |
| `Transformer_TS` | `pytorch_transformer_ts.py` | Transformer (시계열) |
| `Localformer_TS` | `pytorch_localformer_ts.py` | Localformer (시계열) |

#### 딥러닝 — CNN/기타 계열

| 모델 | 파일 | 설명 |
|------|------|------|
| `TCN` | `pytorch_tcn.py` / `tcn.py` | Temporal Convolutional Network |
| `TCN_TS` | `pytorch_tcn_ts.py` | TCN (시계열) |
| `TabNet` | `pytorch_tabnet.py` | Attentive Interpretable Tabular |
| `SFM` | `pytorch_sfm.py` | State Frequency Memory Network |
| `HIST` | `pytorch_hist.py` | Historical Information Stock Transformer |
| `IGMTF` | `pytorch_igmtf.py` | Investment Grade Multi-Task Framework |
| `ADD` | `pytorch_add.py` | Attention-based Decomposition |
| `SANDWICH` | `pytorch_sandwich.py` | Sandwich Network |

#### 딥러닝 — GNN 계열

| 모델 | 파일 | 설명 |
|------|------|------|
| `GATs` | `pytorch_gats.py` | Graph Attention Networks |
| `GATs_TS` | `pytorch_gats_ts.py` | GATs (시계열) |

#### 특수 모델

| 모델 | 파일 | 설명 |
|------|------|------|
| `DoubleEnsemble` | `double_ensemble.py` | 앙상블 학습 (피처+샘플 리웨이팅) |
| `LinearModel` | `linear.py` | 선형 회귀 |
| `TRA` | `pytorch_tra.py` | Temporal Routing Adapter |
| `TCTS` | `pytorch_tcts.py` | Temporal Consistent Training Strategy |
| `ADARNN` | `pytorch_adarnn.py` | Adaptive RNN (도메인 적응) |

#### `_TS` 접미사 모델

`_TS` 접미사 모델은 `TSDatasetH` (시계열 데이터셋)와 함께 사용한다. 일반 모델은 테이블 형태 입력을, `_TS` 모델은 시퀀스 형태 입력을 기대한다.

### 모델 설정 예시

#### Python 코드

```python
from qlib.utils import init_instance_by_config

model_config = {
    "class": "LGBModel",
    "module_path": "qlib.contrib.model.gbdt",
    "kwargs": {
        "loss": "mse",
        "colsample_bytree": 0.8879,
        "learning_rate": 0.2,
        "subsample": 0.8789,
        "lambda_l1": 205.6999,
        "lambda_l2": 580.9768,
        "max_depth": 8,
        "num_leaves": 210,
        "num_threads": 20,
    },
}

model = init_instance_by_config(model_config)
model.fit(dataset)
pred = model.predict(dataset)
```

#### YAML 설정

```yaml
task:
    model:
        class: LGBModel
        module_path: qlib.contrib.model.gbdt
        kwargs:
            loss: mse
            colsample_bytree: 0.8879
            learning_rate: 0.2
            subsample: 0.8789
            lambda_l1: 205.6999
            lambda_l2: 580.9768
            max_depth: 8
            num_leaves: 210
            num_threads: 20
```

---

## 5. 전략 & 백테스팅

### 전략 클래스

#### TopkDropoutStrategy (기본 전략)

상위 K개 종목을 보유하고, 매 거래일마다 n_drop개를 교체하는 전략:

```python
strategy_config = {
    "class": "TopkDropoutStrategy",
    "module_path": "qlib.contrib.strategy.signal_strategy",
    "kwargs": {
        "signal": "<PRED>",  # 또는 (model, dataset)
        "topk": 50,          # 포트폴리오 종목 수
        "n_drop": 5,         # 매일 교체 종목 수
        "method_sell": "bottom",    # 매도 방식: bottom/random
        "method_buy": "top",        # 매수 방식: top/random
        "hold_thresh": 1,           # 최소 보유일
        "only_tradable": False,     # 거래 가능 종목만 고려
        "forbid_all_trade_at_limit": True,  # 상한가/하한가 시 거래 금지
    },
}
```

#### 기타 전략

| 전략 | 설명 |
|------|------|
| `BaseSignalStrategy` | 시그널 기반 전략 추상 클래스 |
| `TopkDropoutStrategy` | Top-K 교체 전략 |
| `EnhancedIndexingOptimizer` | 인덱스 추적 + 알파 최적화 |
| `TWAPStrategy` | 시간 가중 평균 가격 실행 (고빈도) |

### 백테스트 설정

#### Python 코드 기반

```python
from qlib.contrib.evaluate import backtest_daily

report_df, positions = backtest_daily(
    start_time="2017-01-01",
    end_time="2020-08-01",
    strategy=strategy_config,
    account=100000000,           # 초기 자본금 (1억)
    benchmark="SH000300",        # 벤치마크 (CSI300)
    exchange_kwargs={
        "freq": "day",
        "limit_threshold": 0.095,  # 가격 제한폭
        "deal_price": "close",     # 체결가
        "open_cost": 0.0005,       # 매수 수수료
        "close_cost": 0.0015,      # 매도 수수료 (인지세 포함)
        "min_cost": 5,             # 최소 수수료
    },
)
```

#### 리전별 기본 설정

| 설정 | 중국 (CN) | 미국 (US) | 대만 (TW) |
|------|-----------|----------|-----------|
| `trade_unit` | 100주 (1 lot) | 1주 | 1000주 |
| `limit_threshold` | 0.095 (9.5%) | `None` | 0.1 (10%) |
| `deal_price` | `"close"` | `"close"` | `"close"` |

### Long-Short 백테스트

롱-숏 전략 백테스트도 지원한다:

```python
from qlib.contrib.evaluate import long_short_backtest

result = long_short_backtest(
    pred=predictions,
    topk=50,
    shift=1,
    open_cost=0,
    close_cost=0,
)
# result = {"long": Series, "short": Series, "long_short": Series}
```

---

## 6. 워크플로우 & 실험 관리

Qlib은 **두 가지 인터페이스**를 제공한다:
1. **YAML 기반**: `qrun` CLI로 설정 파일 실행
2. **Python 코드 기반**: `R.start()`로 스크립트에서 직접 실행

### YAML 기반 워크플로우 (qrun)

```bash
qrun workflow_config_lightgbm_Alpha158.yaml
```

YAML 파일 구조:

```yaml
qlib_init:
    provider_uri: "~/.qlib/qlib_data/cn_data"
    region: cn

market: &market csi300
benchmark: &benchmark SH000300

data_handler_config: &data_handler_config
    start_time: 2008-01-01
    end_time: 2020-08-01
    fit_start_time: 2008-01-01
    fit_end_time: 2014-12-31
    instruments: *market

port_analysis_config: &port_analysis_config
    strategy:
        class: TopkDropoutStrategy
        module_path: qlib.contrib.strategy
        kwargs:
            signal: <PRED>
            topk: 50
            n_drop: 5
    backtest:
        start_time: 2017-01-01
        end_time: 2020-08-01
        account: 100000000
        benchmark: *benchmark
        exchange_kwargs:
            limit_threshold: 0.095
            deal_price: close
            open_cost: 0.0005
            close_cost: 0.0015
            min_cost: 5

task:
    model:
        class: LGBModel
        module_path: qlib.contrib.model.gbdt
        kwargs:
            loss: mse
            colsample_bytree: 0.8879
            learning_rate: 0.2
            subsample: 0.8789
            lambda_l1: 205.6999
            lambda_l2: 580.9768
            max_depth: 8
            num_leaves: 210
            num_threads: 20
    dataset:
        class: DatasetH
        module_path: qlib.data.dataset
        kwargs:
            handler:
                class: Alpha158
                module_path: qlib.contrib.data.handler
                kwargs: *data_handler_config
            segments:
                train: [2008-01-01, 2014-12-31]
                valid: [2015-01-01, 2016-12-31]
                test: [2017-01-01, 2020-08-01]
    record:
        - class: SignalRecord
          module_path: qlib.workflow.record_temp
          kwargs:
              model: <MODEL>
              dataset: <DATASET>
        - class: SigAnaRecord
          module_path: qlib.workflow.record_temp
          kwargs:
              ana_long_short: False
              ann_scaler: 252
        - class: PortAnaRecord
          module_path: qlib.workflow.record_temp
          kwargs:
              config: *port_analysis_config
```

> **참고**: `<PRED>`, `<MODEL>`, `<DATASET>`는 자동으로 치환되는 플레이스홀더이다.

### Python 코드 기반 워크플로우

```python
import qlib
from qlib.constant import REG_CN
from qlib.utils import init_instance_by_config, flatten_dict
from qlib.workflow import R
from qlib.workflow.record_temp import SignalRecord, PortAnaRecord, SigAnaRecord

# 1. 초기화
qlib.init(provider_uri="~/.qlib/qlib_data/cn_data", region=REG_CN)

# 2. 모델 & 데이터셋 생성
model = init_instance_by_config(model_config)
dataset = init_instance_by_config(dataset_config)

# 3. 실험 시작
with R.start(experiment_name="workflow"):
    # 하이퍼파라미터 기록
    R.log_params(**flatten_dict(task_config))

    # 모델 학습
    model.fit(dataset)
    R.save_objects(**{"params.pkl": model})

    # 예측 (SignalRecord)
    recorder = R.get_recorder()
    sr = SignalRecord(model, dataset, recorder)
    sr.generate()  # pred.pkl, label.pkl 저장

    # 신호 분석 (SigAnaRecord) — IC, ICIR 등
    sar = SigAnaRecord(recorder)
    sar.generate()  # ic.pkl, ric.pkl 저장

    # 포트폴리오 분석 (PortAnaRecord) — 백테스트 + 리스크 분석
    par = PortAnaRecord(recorder, port_analysis_config, "day")
    par.generate()  # report, positions, port_analysis 저장
```

### Record 시스템

Record는 실험 결과를 구조화하여 저장하는 시스템이다.

| Record 클래스 | 생성 파일 | 의존성 | 설명 |
|--------------|----------|--------|------|
| `SignalRecord` | `pred.pkl`, `label.pkl` | 없음 | 모델 예측 결과 저장 |
| `SigAnaRecord` | `ic.pkl`, `ric.pkl` | `SignalRecord` | IC, Rank IC, ICIR 분석 |
| `HFSignalRecord` | `ic.pkl`, `ric.pkl`, `long_short_r.pkl` 등 | `SignalRecord` | 고빈도 신호 분석 |
| `PortAnaRecord` | `report_normal_*.pkl`, `positions_normal_*.pkl`, `port_analysis_*.pkl` | `SignalRecord` | 포트폴리오 백테스트 + 리스크 분석 |
| `MultiPassPortAnaRecord` | `multi_pass_port_analysis_*.pkl` | `SignalRecord` | 다중 패스 백테스트 (초기 포지션 랜덤화) |

### MLflow 실험 추적

Qlib은 기본적으로 MLflow를 사용하여 실험을 추적한다:

```python
from qlib.workflow import R

# 실험 목록 조회
experiments = R.list_experiments()

# 특정 실험의 레코더 조회
recorders = R.list_recorders(experiment_name="workflow")

# 레코더에서 아티팩트 로드
recorder = R.get_recorder(experiment_name="workflow")
pred = recorder.load_object("pred.pkl")
ic = recorder.load_object("sig_analysis/ic.pkl")
report = recorder.load_object("portfolio_analysis/report_normal_1day.pkl")
```

MLflow URI 설정:

```python
qlib.init(
    provider_uri="~/.qlib/qlib_data/cn_data",
    exp_manager={
        "class": "MLflowExpManager",
        "module_path": "qlib.workflow.expm",
        "kwargs": {
            "uri": "file:///path/to/mlruns",
            "default_exp_name": "MyExperiment",
        },
    },
)
```

---

## 7. 평가 & 리포트

### risk_analysis() — 리스크 지표

```python
from qlib.contrib.evaluate import risk_analysis

# report에서 초과 수익률 계산
analysis = risk_analysis(
    report_normal["return"] - report_normal["bench"] - report_normal["cost"],
    freq="day",
)
print(analysis)
```

출력 지표:

| 지표 | 설명 |
|------|------|
| `mean` | 일평균 수익률 |
| `std` | 일간 수익률 표준편차 |
| `annualized_return` | 연환산 수익률 (mean × 238) |
| `information_ratio` | 정보 비율 (IR) ≈ 샤프비율 |
| `max_drawdown` | 최대 낙폭 (MDD) |

`mode` 파라미터로 수익률 누적 방식을 선택할 수 있다:
- `"sum"` (기본): 산술적 누적 (선형)
- `"product"`: 기하학적 누적 (복리)

주파수별 연환산 스케일러:

| 주파수 | 스케일러 (N) |
|--------|-------------|
| 일봉 | 238 |
| 주봉 | 50 |
| 월봉 | 12 |
| 분봉 | 240 × 238 |

### indicator_analysis() — 거래 지표

```python
from qlib.contrib.evaluate import indicator_analysis

analysis = indicator_analysis(indicators_normal, method="mean")
```

| 지표 | 설명 |
|------|------|
| `pa` | Price Advantage — 가격 우위 |
| `pos` | Positive Rate — 양의 수익률 비율 |
| `ffr` | Fulfill Rate — 체결률 |

`method` 옵션:
- `"mean"`: 단순 평균
- `"amount_weighted"`: 거래량 가중 평균
- `"value_weighted"`: 거래대금 가중 평균

### SigAnaRecord — 신호 품질 분석

SigAnaRecord가 자동으로 계산하고 기록하는 지표:

| 지표 | 설명 |
|------|------|
| IC | Information Coefficient (예측-실제 상관) |
| ICIR | IC / IC의 표준편차 (안정성) |
| Rank IC | 순위 기반 IC |
| Rank ICIR | 순위 기반 ICIR |
| Long-Short Ann Return | 롱숏 연환산 수익률 (선택적) |
| Long-Short Ann Sharpe | 롱숏 연환산 샤프 (선택적) |

---

## 8. 고급 기능

### Rolling/Online 학습

시간이 지남에 따라 모델을 주기적으로 재학습하는 롤링 학습:

```bash
# examples/model_rolling/ 참조
cd examples/model_rolling/
```

주요 개념:
- **Rolling Retrain**: 고정된 윈도우로 주기적 재학습
- **Rolling Finetune**: 기존 모델 기반 미세 조정
- **Online Update**: 실시간 업데이트

### 강화학습 (RL) 모듈

주문 실행 최적화를 위한 RL 모듈. `qlib/rl/` 디렉토리에 구현되어 있다.

```bash
# 설치 (추가 의존성)
pip install pyqlib[rl]  # tianshou, torch 등 필요
# 주의: numpy<2.0 필요
```

예제: `examples/rl/` 및 `examples/rl_order_execution/`

### 하이퍼파라미터 튜닝

```bash
# examples/hyperparameter/ 참조
cd examples/hyperparameter/
```

### 커스텀 데이터 변환

CSV 등의 원시 데이터를 Qlib 바이너리 포맷으로 변환:

```bash
# CSV → Qlib 바이너리 포맷
python scripts/dump_bin.py dump_all \
    --csv_path <csv_directory> \
    --qlib_dir ~/.qlib/qlib_data/my_data \
    --freq day \
    --include_fields open,close,high,low,volume,factor
```

### 메타러닝

데이터 선택을 위한 메타러닝 프레임워크. `examples/benchmarks_dynamic/` 참조.

---

## 9. 유틸리티 스크립트

### scripts/ 디렉토리

| 스크립트 | 설명 |
|----------|------|
| `get_data.py` | Qlib 바이너리 데이터 다운로드 |
| `dump_bin.py` | CSV → Qlib 바이너리 포맷 변환 |
| `dump_pit.py` | PIT 데이터 덤프 |
| `check_data_health.py` | 데이터 무결성 검사 |
| `check_dump_bin.py` | 바이너리 덤프 결과 검증 |
| `collect_info.py` | 데이터 수집 정보 |
| `data_collector/` | Yahoo Finance 등에서 데이터 수집 |

### examples/ 디렉토리

| 디렉토리 | 설명 |
|----------|------|
| `benchmarks/` | 모델별 벤치마크 설정 (LightGBM, LSTM, Transformer 등) |
| `benchmarks_dynamic/` | 동적 벤치마크 (메타러닝 등) |
| `workflow_by_code.py` | Python 코드 기반 워크플로우 예제 |
| `workflow_by_code.ipynb` | Jupyter Notebook 버전 |
| `model_rolling/` | 롤링 학습 예제 |
| `hyperparameter/` | 하이퍼파라미터 튜닝 예제 |
| `portfolio/` | 포트폴리오 관리 예제 |
| `highfreq/` | 고빈도 거래 예제 |
| `rl/` | 강화학습 예제 |
| `rl_order_execution/` | RL 주문 실행 예제 |
| `nested_decision_execution/` | 중첩 실행 예제 |
| `online_srv/` | 온라인 서빙 예제 |
| `tutorial/` | 튜토리얼 자료 |
| `data_demo/` | 데이터 데모 |
| `run_all_model.py` | 전체 모델 실행 스크립트 |

### 전체 워크플로우 예제 (최소 코드)

```python
import qlib
from qlib.constant import REG_CN
from qlib.utils import init_instance_by_config
from qlib.workflow import R
from qlib.workflow.record_temp import SignalRecord, SigAnaRecord, PortAnaRecord

# 1. 초기화
qlib.init(provider_uri="~/.qlib/qlib_data/cn_data", region=REG_CN)

# 2. 태스크 설정
task = {
    "model": {
        "class": "LGBModel",
        "module_path": "qlib.contrib.model.gbdt",
        "kwargs": {"loss": "mse", "learning_rate": 0.2, "num_leaves": 210},
    },
    "dataset": {
        "class": "DatasetH",
        "module_path": "qlib.data.dataset",
        "kwargs": {
            "handler": {
                "class": "Alpha158",
                "module_path": "qlib.contrib.data.handler",
                "kwargs": {
                    "instruments": "csi300",
                    "start_time": "2008-01-01",
                    "end_time": "2020-08-01",
                    "fit_start_time": "2008-01-01",
                    "fit_end_time": "2014-12-31",
                },
            },
            "segments": {
                "train": ["2008-01-01", "2014-12-31"],
                "valid": ["2015-01-01", "2016-12-31"],
                "test": ["2017-01-01", "2020-08-01"],
            },
        },
    },
}

# 3. 모델 & 데이터셋 생성
model = init_instance_by_config(task["model"])
dataset = init_instance_by_config(task["dataset"])

# 4. 실험 실행
with R.start(experiment_name="lightgbm_alpha158"):
    model.fit(dataset)

    recorder = R.get_recorder()

    # 예측
    sr = SignalRecord(model, dataset, recorder)
    sr.generate()

    # IC 분석
    sar = SigAnaRecord(recorder)
    sar.generate()

    # 백테스트
    par = PortAnaRecord(recorder, config={
        "strategy": {
            "class": "TopkDropoutStrategy",
            "module_path": "qlib.contrib.strategy",
            "kwargs": {"signal": "<PRED>", "topk": 50, "n_drop": 5},
        },
        "backtest": {
            "start_time": "2017-01-01",
            "end_time": "2020-08-01",
            "account": 100000000,
            "benchmark": "SH000300",
            "exchange_kwargs": {
                "limit_threshold": 0.095,
                "deal_price": "close",
                "open_cost": 0.0005,
                "close_cost": 0.0015,
                "min_cost": 5,
            },
        },
    })
    par.generate()
```

---

## 10. 자주 묻는 질문 / 트러블슈팅

### 데이터 경로 문제

**Q: `data not found` 오류가 발생한다**

```
WARN: data not found for ...
```

A: 데이터가 올바르게 다운로드되었는지 확인한다:
```bash
ls ~/.qlib/qlib_data/cn_data/
# calendars/, instruments/, features/ 디렉토리가 있어야 함

# 데이터 재다운로드
python scripts/get_data.py qlib_data --target_dir ~/.qlib/qlib_data/cn_data
```

**Q: `provider_uri` 설정이 반영되지 않는다**

A: `provider_uri` 우선순위를 확인한다:
1. `backend_config`의 `provider_uri` (최우선)
2. `backend_config`의 `provider_uri_map`
3. `qlib.init()`의 `provider_uri`

### Cython 컴파일 오류

**Q: `rolling` 또는 `expanding` import 에러**

```
Do not import qlib package in the repository directory
```

A: qlib 소스 디렉토리 안에서 import하지 말고, 다른 경로에서 실행한다. 또는 Cython 확장을 먼저 컴파일한다:
```bash
make prerequisite
# 또는
cd qlib/data/_libs/ && python setup.py build_ext --inplace
```

**Q: numpy 버전 관련 ValueError**

A: Cython 확장은 numpy 버전에 민감하다. RL 모듈 사용 시 `numpy<2.0`이 필요하다:
```bash
pip install "numpy<2.0"
make prerequisite  # 재컴파일
```

### 메모리/캐시 관련

**Q: 메모리 사용량이 너무 크다**

A: 메모리 캐시 설정을 조정한다:
```python
qlib.init(
    provider_uri="~/.qlib/qlib_data/cn_data",
    mem_cache_size_limit=100,      # 기본값 500에서 줄임
    mem_cache_limit_type="length", # "length" 또는 "sizeof"
    mem_cache_expire=1800,         # 30분 만료 (기본 1시간)
)
```

**Q: Redis 연결 실패 경고**

```
redis connection failed, DiskExpressionCache will not be used
```

A: 디스크 캐시를 사용하려면 Redis가 필요하다. Redis 없이 사용하려면 캐시를 비활성화한다 (client 모드 기본):
```python
qlib.init(
    provider_uri="~/.qlib/qlib_data/cn_data",
    expression_cache=None,
    dataset_cache=None,
)
```

### 기타

**Q: `qrun`이 안 된다**

A: `pip install pyqlib`으로 설치 시 `qrun` CLI가 함께 설치된다. 진입점은 `qlib.cli.run:run`이다.
```bash
pip install -e .  # 또는 pip install pyqlib
qrun your_config.yaml
```

**Q: 여러 실험을 비교하고 싶다**

A: MLflow UI를 사용한다:
```bash
mlflow ui --backend-store-uri file:///path/to/mlruns
# 브라우저에서 http://localhost:5000 접속
```

**Q: 커스텀 연산자를 추가하고 싶다**

A: `ExpressionOps`를 상속한 클래스를 만들고 `custom_ops`로 등록한다:
```python
from qlib.data.ops import ElemOperator

class MyOp(ElemOperator):
    def _load_internal(self, instrument, start_index, end_index, *args):
        series = self.feature.load(instrument, start_index, end_index, *args)
        return series * 2  # 예시: 2배

qlib.init(
    provider_uri="~/.qlib/qlib_data/cn_data",
    custom_ops=[MyOp],
)
```
