// qlib CI/CD pipeline (Blue/Green deploy via deploy.sh).
// Mirrors likeweb/pm Jenkinsfile structure.
//
// Required Jenkins config:
//   - agent labels: build-agent-135 (image build), build-agent (deploy)
//   - credentials:  github-key-likeweb (SSH for git push to remote)
//   - registry:     127.0.0.1:5000  (in-cluster)

pipeline {
    agent none

    environment {
        REPO_URL   = 'https://github.com/BenjaminOh/qlib.git'
        APP_DIR    = '/home/qlib'
        REMOTE     = 'root@112.175.30.76'                        // rocky-monitor host
        REGISTRY   = '127.0.0.1:5000'
        RECIPIENTS = 'ohsjwe@likeweb.co.kr, crazin@likeweb.co.kr'
    }

    stages {
        stage('Extract Git Info') {
            agent { label 'build-agent-135' }
            steps {
                script {
                    env.GIT_BRANCH = sh(script: "git rev-parse --abbrev-ref HEAD", returnStdout: true).trim()
                    env.GIT_BRANCHSTRIP = env.GIT_BRANCH
                        .replaceFirst(/^origin\//, '')
                        .replaceFirst(/^refs\/heads\//, '')

                    env.GIT_COMMIT_HASH    = sh(script: "git rev-parse HEAD", returnStdout: true).trim()
                    env.GIT_COMMIT_AUTHOR  = sh(script: "git log -1 --pretty=format:'%an'", returnStdout: true).trim()
                    env.GIT_COMMIT_EMAIL   = sh(script: "git log -1 --pretty=format:'%ae'", returnStdout: true).trim()
                    env.GIT_COMMIT_MESSAGE = sh(script: "git log -1 --pretty=format:'%s'", returnStdout: true).trim()
                    env.GIT_COMMIT_TIME    = sh(script: "git log -1 --pretty=format:'%cd' --date=format:'%Y-%m-%d %H:%M:%S'", returnStdout: true).trim()

                    if (env.GIT_BRANCHSTRIP != 'main' && env.GIT_BRANCHSTRIP != 'develop') {
                        error "지원되지 않는 브랜치입니다: ${env.GIT_BRANCHSTRIP}"
                    }

                    echo "🔎 브랜치: ${env.GIT_BRANCHSTRIP}"
                    echo "🔎 커밋: ${env.GIT_COMMIT_HASH}"
                    echo "🔎 작성자: ${env.GIT_COMMIT_AUTHOR} <${env.GIT_COMMIT_EMAIL}>"
                    echo "🔎 메시지: ${env.GIT_COMMIT_MESSAGE}"
                    echo "🔎 시간: ${env.GIT_COMMIT_TIME}"
                }
            }
        }

        stage('Fetch .env from REMOTE') {
            agent { label 'build-agent-135' }
            steps {
                sh """
                    scp -o StrictHostKeyChecking=no ${REMOTE}:${APP_DIR}/.env ./.env
                    echo '✅ .env 복사 완료'
                """
            }
        }

        stage('Build & Push Images') {
            agent { label 'build-agent-135' }
            steps {
                sh """
                    echo '🔨 이미지 빌드'
                    IMAGE_TAG=${BUILD_NUMBER} docker compose -f docker-compose.prod.yml build --parallel api web

                    echo '🏷  latest 태깅'
                    docker tag ${REGISTRY}/qlib-api:${BUILD_NUMBER} ${REGISTRY}/qlib-api:latest
                    docker tag ${REGISTRY}/qlib-web:${BUILD_NUMBER} ${REGISTRY}/qlib-web:latest

                    echo '📤 Registry push'
                    docker push ${REGISTRY}/qlib-api:${BUILD_NUMBER}
                    docker push ${REGISTRY}/qlib-api:latest
                    docker push ${REGISTRY}/qlib-web:${BUILD_NUMBER}
                    docker push ${REGISTRY}/qlib-web:latest
                """
            }
        }

        stage('Deploy') {
            agent { label 'build-agent' }
            steps {
                script {
                    def branchName = env.GIT_BRANCHSTRIP
                    // Public repo over HTTPS — no credential helper needed.
                    sh """
                        set -e
                        if [ ! -d "${env.APP_DIR}/.git" ]; then
                            echo "📂 최초 클론: ${env.APP_DIR}"
                            mkdir -p "${env.APP_DIR}"
                            git clone ${env.REPO_URL} "${env.APP_DIR}"
                        fi

                        cd ${env.APP_DIR}
                        # Make sure local origin is HTTPS too (in case it was cloned via SSH earlier)
                        git remote set-url origin ${env.REPO_URL}
                        git fetch origin
                        git reset --hard origin/${branchName}
                        git clean -fd --exclude=data/
                        chmod +x deploy.sh
                        IMAGE_TAG=${BUILD_NUMBER} ./deploy.sh
                    """
                }
            }
        }
    }

    post {
        success { sendMailOnSuccess() }
        failure { sendMailOnFailure("❌ 파이프라인 실패") }
        always  { echo "🧹 Workspace 정리 완료" }
    }
}

def sendMailOnFailure(message) {
    emailext (
        subject: "🔴 qlib 빌드 실패: ${env.JOB_NAME} #${env.BUILD_NUMBER} (${env.GIT_BRANCHSTRIP})",
        body: """
        <h2>❌ qlib Jenkins 빌드 실패</h2>
        <p>브랜치: ${env.GIT_BRANCHSTRIP}</p>
        <p>커밋: ${env.GIT_COMMIT_MESSAGE ?: 'N/A'}</p>
        <p>작성자: ${env.GIT_COMMIT_AUTHOR ?: 'N/A'} &lt;${env.GIT_COMMIT_EMAIL ?: 'N/A'}&gt;</p>
        <p>에러: ${message}</p>
        <p><a href="${env.BUILD_URL}console">로그 보기</a></p>
        """,
        to: "${env.RECIPIENTS}",
        from: "no-reply@likeweb.co.kr"
    )
}

def sendMailOnSuccess() {
    emailext (
        subject: "✅ qlib 빌드 성공: ${env.JOB_NAME} #${env.BUILD_NUMBER} (${env.GIT_BRANCHSTRIP})",
        body: """
        <h2>🎉 qlib Blue/Green 배포 완료</h2>
        <p>브랜치: ${env.GIT_BRANCHSTRIP}</p>
        <p>커밋: ${env.GIT_COMMIT_MESSAGE ?: 'N/A'}</p>
        <p>작성자: ${env.GIT_COMMIT_AUTHOR ?: 'N/A'} &lt;${env.GIT_COMMIT_EMAIL ?: 'N/A'}&gt;</p>
        <p><a href="${env.BUILD_URL}console">로그 보기</a></p>
        """,
        to: "${env.RECIPIENTS}",
        from: "no-reply@likeweb.co.kr"
    )
}
