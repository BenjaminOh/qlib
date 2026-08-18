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

    options {
        // 2026-08-19: builds 91 and 92 overlapped and both ran deploy.sh
        // against the same `qlib-blue` compose project. 91 finished, 92 stalled
        // 30+ minutes in the image build. Queue them instead.
        disableConcurrentBuilds()
        timeout(time: 40, unit: 'MINUTES')
        buildDiscarder(logRotator(numToKeepStr: '30'))
    }

    stages {
        // Gate: run tests/app inside Dockerfile.prod's `test` target before
        // anything touches a running slot. A failure here means Deploy never
        // starts, so the live containers keep serving the previous image.
        stage('Test') {
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
