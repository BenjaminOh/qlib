// qlib CI/CD pipeline — mirrors tennis-cms pattern (single-server build).
// No registry, no multi-agent. Jenkins SSH Pipeline plugin connects to the
// host over the Docker bridge (172.17.0.1) and lets deploy.sh handle build
// + Blue/Green flip + nginx-waf reload all on the host itself.

// Both stages talk to the same host with the same key. The identityFile is
// only valid inside a withCredentials block, so the map is built per stage
// from here rather than shared as a global.
def makeRemote(identityFile) {
    def remote = [:]
    remote.name = 'qlib Production'
    remote.host = '172.17.0.1'      // Docker bridge → host
    remote.user = 'root'
    remote.allowAnyHosts = true
    remote.identityFile = identityFile
    return remote
}

pipeline {
    agent any

    // 2026-08-27: WAF 가 GitHub 웹훅을 6일간 막아 19커밋이 배포되지 않았다.
    // ModSecurity(OWASP CRS 4.22.0) 규칙 949110 — 이상점수 25 >= 10 으로 403.
    // qlib 저장소만 걸렸고 apnhi·aphennet 은 200 이라, 공용 WAF 를 손대는 대신
    // **폴링 백업**을 둔다. 웹훅이 살아 있으면 웹훅이 먼저 잡고, 죽으면 폴링이
    // 최대 5분 늦게 잡는다. UI 설정이 아니라 여기 두는 이유: 버전 관리되고
    // 리뷰되며, Jenkins 잡 설정 드리프트가 생기지 않는다.
    //
    // ⚠ 파이프라인 triggers 는 **빌드가 한 번 돌아야 등록된다.** 이 블록을
    // 추가한 뒤 첫 빌드는 수동(Build Now)으로 띄워야 하고, 그 뒤로는 폴링이
    // 스스로 이어간다.
    triggers {
        pollSCM('H/5 * * * *')
    }

    options {
        // 2026-08-19: builds 91 and 92 overlapped and both ran deploy.sh
        // against the same `qlib-blue` compose project. 91 finished, 92 stalled
        // 30+ minutes in the image build. Queue them instead.
        disableConcurrentBuilds()
        // Backstop only — NOT a tuning knob. Real builds run 24-37 min on this
        // host (#90 24, #91 29, #92 37, #94 34). A 40-min limit aborted #93
        // mid-deploy, right after "기존 green 컨테이너 정리" — i.e. while
        // containers were being torn down. Blue happened to still be serving,
        // but an abort a few steps later (after the nginx flip, before the new
        // slot is healthy) would have been an outage. The per-stage timeout on
        // Test catches a hung gate; Deploy must never be interrupted midway.
        timeout(time: 120, unit: 'MINUTES')
        buildDiscarder(logRotator(numToKeepStr: '30'))
    }

    stages {
        // Gate: run tests/app inside Dockerfile.prod's `test` target before
        // anything touches a running slot. A failure here means Deploy never
        // starts, so the live containers keep serving the previous image.
        stage('Test') {
            // The gate itself is fast (24s of pytest). If it ever hangs, kill it
            // here rather than letting the pipeline-level backstop fire during
            // the Deploy stage.
            options { timeout(time: 25, unit: 'MINUTES') }
            when {
                anyOf {
                    branch 'main'
                    expression { env.BRANCH_NAME == null }
                }
            }
            steps {
                script {
                    withCredentials([sshUserPrivateKey(
                        credentialsId: 'deploy-key',
                        keyFileVariable: 'IDENTITY_FILE',
                        usernameVariable: 'USER'
                    )]) {
                        echo "🧪 Running test gate (tests/app)..."
                        sshCommand remote: makeRemote(IDENTITY_FILE), command: """
                            set -e
                            cd /home/qlib
                            git fetch --all
                            git reset --hard origin/main
                            # --output type=cacheonly: run the stage, keep the
                            # layer cache, but never materialise/unpack an image.
                            # Tagging it cost ~8 min of "exporting → unpacking" on
                            # a multi-GB stage we throw away anyway (observed on
                            # build #93). The RUN still fails the build on a red
                            # test — verified both directions.
                            docker buildx build -f Dockerfile.prod --target test \
                                                --output type=cacheonly .
                        """
                    }
                }
            }
        }

        stage('Deploy') {
            steps {
                script {
                    withCredentials([sshUserPrivateKey(
                        credentialsId: 'deploy-key',
                        keyFileVariable: 'IDENTITY_FILE',
                        usernameVariable: 'USER'
                    )]) {
                        if (env.BRANCH_NAME == 'main' || env.BRANCH_NAME == null) {
                            echo "🚀 Deploying qlib to Production (main)..."
                            // The checkout is repeated on purpose: Deploy must be
                            // safe to replay on its own, without the Test stage.
                            sshCommand remote: makeRemote(IDENTITY_FILE), command: """
                                set -e
                                cd /home/qlib
                                git fetch --all
                                git reset --hard origin/main
                                chmod +x deploy.sh
                                # IMAGE_TAG 미지정 → deploy.sh가 호스트에서 직접 build
                                ./deploy.sh
                            """
                        } else {
                            echo "Skipping deployment for branch: ${env.BRANCH_NAME}"
                        }
                    }
                }
            }
        }
    }

    post {
        success {
            echo "✅ qlib deployment successful"
            mail to: 'ohsjwe@gmail.com',
                 from: 'qlib <noreply@tmanager.kr>',
                 subject: "✅ qlib Build Success: ${env.JOB_NAME} #${env.BUILD_NUMBER}",
                 body: """
Build Successful!
Project: ${env.JOB_NAME}
Build Number: #${env.BUILD_NUMBER}
Branch: ${env.BRANCH_NAME ?: 'main'}
Build URL: ${env.BUILD_URL}

Smoke test:
  curl -sI  https://qlib.tmanager.kr/
  curl -s   https://qlib.tmanager.kr/api/v1/health
"""
        }
        failure {
            echo "❌ qlib deployment failed"
            mail to: 'ohsjwe@gmail.com',
                 from: 'qlib <noreply@tmanager.kr>',
                 subject: "❌ qlib Build Failed: ${env.JOB_NAME} #${env.BUILD_NUMBER}",
                 body: """
Build Failed!
Project: ${env.JOB_NAME}
Build Number: #${env.BUILD_NUMBER}
Branch: ${env.BRANCH_NAME ?: 'main'}
Build URL: ${env.BUILD_URL}

Check console output:
  ${env.BUILD_URL}console
"""
        }
    }
}
