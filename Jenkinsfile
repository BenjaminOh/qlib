// qlib CI/CD pipeline — mirrors tennis-cms pattern (single-server build).
// No registry, no multi-agent. Jenkins SSH Pipeline plugin connects to the
// host over the Docker bridge (172.17.0.1) and lets deploy.sh handle build
// + Blue/Green flip + nginx-waf reload all on the host itself.

pipeline {
    agent any

    stages {
        stage('Deploy') {
            steps {
                script {
                    def remote = [:]
                    remote.name = 'qlib Production'
                    remote.host = '172.17.0.1'      // Docker bridge → rocky-monitor host
                    remote.user = 'root'
                    remote.allowAnyHosts = true

                    // Jenkins Credentials → 'deploy-key' (already registered for tennis-cms etc.)
                    withCredentials([sshUserPrivateKey(
                        credentialsId: 'deploy-key',
                        keyFileVariable: 'IDENTITY_FILE',
                        usernameVariable: 'USER'
                    )]) {
                        remote.identityFile = IDENTITY_FILE

                        if (env.BRANCH_NAME == 'main' || env.BRANCH_NAME == null) {
                            echo "🚀 Deploying qlib to Production (main)..."
                            sshCommand remote: remote, command: """
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
