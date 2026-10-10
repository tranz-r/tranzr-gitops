{{/*
Expand the name of the chart.
*/}}
{{- define "tranzrmoves.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
We truncate at 63 chars because some Kubernetes name fields are limited to this (by the DNS naming spec).
If release name contains chart name it will be used as a full name.
*/}}
{{- define "tranzrmoves.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "tranzrmoves.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "tranzrmoves.labels" -}}
helm.sh/chart: {{ include "tranzrmoves.chart" . }}
{{ include "tranzrmoves.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "tranzrmoves.selectorLabels" -}}
app.kubernetes.io/name: {{ include "tranzrmoves.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Create the name of the service account to use
*/}}
{{- define "tranzrmoves.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "tranzrmoves.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Resolve container image reference from .Values.images.<key>.
Per-image tag overrides images.movesVersion (used by gateway).
Usage: {{ include "tranzrmoves.imageRef" (dict "root" . "key" "movesServices") }}
*/}}
{{- define "tranzrmoves.imageRef" -}}
{{- $root := .root -}}
{{- $key := .key -}}
{{- $img := required (printf "values.images.%s is required" $key) (index $root.Values.images $key) -}}
{{- $tag := $img.tag | default $root.Values.images.movesVersion | default $root.Chart.AppVersion -}}
{{- printf "%s:%s" $img.repository $tag -}}
{{- end }}

{{/*
Default pull policy for Tranzr container images.
*/}}
{{- define "tranzrmoves.imagePullPolicy" -}}
{{- .Values.images.pullPolicy | default "IfNotPresent" -}}
{{- end }}

{{/*
Platform Redis/RabbitMQ password env vars (hosts come from .Values.platformMessaging).
Usage: {{ include "tranzrmoves.platformMessagingPasswordEnv" (dict "root" . "redis" true "rabbitmq" false) | nindent 12 }}
*/}}
{{- define "tranzrmoves.platformMessagingPasswordEnv" -}}
{{- $root := .root -}}
{{- $redis := .redis | default false -}}
{{- $rabbitmq := .rabbitmq | default false -}}
{{- if $root.Values.platformMessaging.enabled }}
{{- if $redis }}
- name: REDIS_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ $root.Values.externalSecrets.name }}
      key: platform-redis-password
{{- end }}
{{- if $rabbitmq }}
- name: RABBITMQ_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ $root.Values.externalSecrets.name }}
      key: platform-rabbitmq-password
{{- end }}
{{- end }}
{{- end }}

{{/*
K8s secret key for app DB ConnectionStrings__TranzrMovesDatabaseConnection.
Staging defaults to session pooler; production overrides to transaction pooler.
Usage: {{ include "tranzrmoves.appDatabaseSecretKey" (dict "root" $ "item" .) }}
*/}}
{{- define "tranzrmoves.appDatabaseSecretKey" -}}
{{- $root := .root -}}
{{- $item := .item -}}
{{- if eq $item.name "ConnectionStrings__TranzrMovesDatabaseConnection" -}}
{{- $root.Values.database.appConnectionSecretKey -}}
{{- else -}}
{{- $item.secretKey -}}
{{- end -}}
{{- end }}

{{/*
Assemble ConnectionStrings from cluster DNS + platform passwords, optional DB port/pool, then exec dotnet.
Optional .database map overrides root Values.database.* for one workload (hasKey so explicit
false for appNoResetOnClose is preserved — never use default that collapses false→true).
Usage: {{ include "tranzrmoves.platformMessagingStartup" (dict "root" . "entrypoint" "TranzrMoves.Api.dll" "redis" true "rabbitmq" false) | nindent 10 }}
Usage with override: {{ include "tranzrmoves.platformMessagingStartup" (dict "root" . "entrypoint" "TranzrMoves.Worker.dll" "redis" false "rabbitmq" true "database" $worker.database) | nindent 10 }}
*/}}
{{- define "tranzrmoves.platformMessagingStartup" -}}
{{- $root := .root -}}
{{- $entrypoint := .entrypoint -}}
{{- $redis := .redis | default false -}}
{{- $rabbitmq := .rabbitmq | default false -}}
{{- $dbOverride := .database | default dict -}}
{{- $dbPort := $root.Values.database.appConnectionPort -}}
{{- $dbPool := $root.Values.database.appMaximumPoolSize -}}
{{- $dbNoReset := $root.Values.database.appNoResetOnClose -}}
{{- if hasKey $dbOverride "appConnectionPort" -}}
{{- $dbPort = index $dbOverride "appConnectionPort" -}}
{{- end -}}
{{- if hasKey $dbOverride "appMaximumPoolSize" -}}
{{- $dbPool = index $dbOverride "appMaximumPoolSize" -}}
{{- end -}}
{{- if hasKey $dbOverride "appNoResetOnClose" -}}
{{- $dbNoReset = index $dbOverride "appNoResetOnClose" -}}
{{- end -}}
{{- $augmentDb := or $dbPort $dbPool $dbNoReset -}}
{{- if or (and $root.Values.platformMessaging.enabled (or $redis $rabbitmq)) $augmentDb }}
command: ["/bin/sh", "-c"]
args:
  - |
    {{- if and $root.Values.platformMessaging.enabled $redis }}
    export ConnectionStrings__redis="{{ $root.Values.platformMessaging.redis.host }}:{{ $root.Values.platformMessaging.redis.port }},password=${REDIS_PASSWORD}"
    {{- end }}
    {{- if and $root.Values.platformMessaging.enabled $rabbitmq }}
    export ConnectionStrings__rabbitmq="amqp://{{ $root.Values.platformMessaging.rabbitmq.username }}:${RABBITMQ_PASSWORD}@{{ $root.Values.platformMessaging.rabbitmq.host }}:{{ $root.Values.platformMessaging.rabbitmq.port }}"
    {{- end }}
    {{- if $augmentDb }}
    {{- if eq (int $dbPort) 5432 }}
    # Keyword Npgsql form: apply chart overrides (session pooler).
    {{- else }}
    # Keyword Npgsql form: apply chart overrides (production transaction pooler).
    {{- end }}
    _cs="${ConnectionStrings__TranzrMovesDatabaseConnection}"
    _cs="$(printf '%s' "$_cs" | sed -E 's/;?[Pp]ort=[^;]*//g; s/;?[Mm]aximum [Pp]ool [Ss]ize=[^;]*//g; s/;?MaxPoolSize=[^;]*//g; s/;?[Nn]o [Rr]eset [Oo]n [Cc]lose=[^;]*//g; s/;;+/;/g; s/^;//; s/;$//')"
    {{- if $dbPort }}
    _cs="${_cs};Port={{ $dbPort }}"
    {{- end }}
    {{- if $dbPool }}
    _cs="${_cs};Maximum Pool Size={{ $dbPool }}"
    {{- end }}
    {{- if $dbNoReset }}
    _cs="${_cs};No Reset On Close=true"
    {{- end }}
    export ConnectionStrings__TranzrMovesDatabaseConnection="${_cs}"
    {{- end }}
    exec dotnet {{ $entrypoint }}
{{- end }}
{{- end }}

{{/*
OpenTelemetry exporter env for instrumented Tranzr workloads.
Usage: {{ include "tranzrmoves.observabilityEnv" (dict "root" . "serviceName" "tranzr-moves-api") | nindent 12 }}
*/}}
{{- define "tranzrmoves.observabilityEnv" -}}
{{- $root := .root -}}
{{- $serviceName := required "serviceName is required" .serviceName -}}
{{- if $root.Values.observability.enabled }}
- name: OTEL_SERVICE_NAME
  value: {{ $serviceName | quote }}
- name: OTEL_EXPORTER_OTLP_ENDPOINT
  value: {{ $root.Values.observability.otlpEndpoint | quote }}
- name: OTEL_EXPORTER_OTLP_PROTOCOL
  value: {{ $root.Values.observability.otlpProtocol | quote }}
- name: OTEL_RESOURCE_ATTRIBUTES
  value: deployment.environment={{ $root.Values.observability.environment }}
{{- end }}
{{- end }}

{{/*
Vision catalogue-learning gates: category-aware true; candidate learning /
admin review / manufacturer lookup remain false.
Usage: {{ include "tranzrmoves.visionCatalogueLearningEnv" (dict "root" .) | nindent 12 }}
*/}}
{{- define "tranzrmoves.visionCatalogueLearningEnv" -}}
{{- $v := .root.Values.features.vision.catalogueLearning -}}
- name: Vision__CatalogueLearning__CategoryAwareInferenceEnabled
  value: {{ $v.categoryAwareInferenceEnabled | quote }}
- name: Vision__CatalogueLearning__CandidateLearningEnabled
  value: {{ $v.candidateLearningEnabled | quote }}
- name: Vision__CatalogueLearning__AdminReviewEnabled
  value: {{ $v.adminReviewEnabled | quote }}
- name: Vision__CatalogueLearning__ManufacturerLookupEnabled
  value: {{ $v.manufacturerLookupEnabled | quote }}
{{- end }}

{{/*
Vision V1 env for the API (publish / hub / media container). Outside backend.env.
Usage: {{ include "tranzrmoves.visionApiEnv" (dict "root" .) | nindent 12 }}
*/}}
{{- define "tranzrmoves.visionApiEnv" -}}
{{- $root := .root -}}
{{- $v := $root.Values.features.vision -}}
- name: Vision__Analysis__MessagingEnabled
  value: {{ $v.analysis.apiMessagingEnabled | quote }}
- name: Vision__Analysis__IncludeConsumer
  value: {{ $v.analysis.includeConsumer | quote }}
- name: Vision__Analysis__HubEnabled
  value: {{ $v.analysis.hubEnabled | quote }}
- name: Vision__Analysis__QueueName
  value: {{ $v.analysis.queueName | quote }}
- name: Vision__Provider
  value: {{ $v.provider.api | quote }}
- name: Vision__Media__Container
  value: {{ $v.media.container | quote }}
- name: Vision__Media__NormalizationPolicyVersion
  value: {{ $v.media.normalizationPolicyVersion | quote }}
- name: Vision__Normalization__NormalizationPolicyVersion
  value: {{ $v.media.normalizationPolicyVersion | quote }}
{{- range $index, $contentType := $v.media.allowedContentTypes }}
- name: Vision__Media__AllowedContentTypes__{{ $index }}
  value: {{ $contentType | quote }}
{{- end }}
- name: Vision__ConfirmationReview__ActivationEnabled
  value: {{ $v.confirmationReview.activationEnabled | quote }}
{{- include "tranzrmoves.visionCatalogueLearningEnv" (dict "root" $root) | nindent 0 }}
{{- end }}

{{/*
Vision V1 env for the processor worker (consume / OpenRouter bounds). Outside workerProcessor.env.
Usage: {{ include "tranzrmoves.visionProcessorEnv" (dict "root" .) | nindent 12 }}
*/}}
{{- define "tranzrmoves.visionProcessorEnv" -}}
{{- $root := .root -}}
{{- $v := $root.Values.features.vision -}}
- name: Vision__Analysis__MessagingEnabled
  value: {{ $v.analysis.processorMessagingEnabled | quote }}
- name: Vision__Analysis__QueueName
  value: {{ $v.analysis.queueName | quote }}
- name: Vision__Provider
  value: {{ $v.provider.processor | quote }}
- name: Vision__OpenRouter__BaseUrl
  value: {{ $v.openRouter.baseUrl | quote }}
- name: Vision__OpenRouter__TimeoutSeconds
  value: {{ $v.openRouter.timeoutSeconds | quote }}
- name: Vision__OpenRouter__MaxTokens
  value: {{ $v.openRouter.maxTokens | quote }}
- name: Vision__OpenRouter__MaxResponseBodyBytes
  value: {{ $v.openRouter.maxResponseBodyBytes | quote }}
- name: Vision__OpenRouter__MaxConcurrentRequests
  value: {{ $v.openRouter.maxConcurrentRequests | quote }}
- name: Vision__Media__Container
  value: {{ $v.media.container | quote }}
- name: Vision__Media__NormalizationPolicyVersion
  value: {{ $v.media.normalizationPolicyVersion | quote }}
- name: Vision__Normalization__NormalizationPolicyVersion
  value: {{ $v.media.normalizationPolicyVersion | quote }}
{{- include "tranzrmoves.visionCatalogueLearningEnv" (dict "root" $root) | nindent 0 }}
{{- end }}

{{/*
Vision V1 env for the scheduler worker (retention sweeper). Outside workerScheduler.env.
Usage: {{ include "tranzrmoves.visionSchedulerEnv" (dict "root" .) | nindent 12 }}
*/}}
{{- define "tranzrmoves.visionSchedulerEnv" -}}
{{- $root := .root -}}
{{- $v := $root.Values.features.vision -}}
- name: Vision__Media__RetentionWorkerEnabled
  value: {{ $v.media.retentionWorkerEnabled | quote }}
- name: Vision__Media__Container
  value: {{ $v.media.container | quote }}
- name: Vision__Media__PolicyVersion
  value: {{ $v.media.policyVersion | quote }}
- name: Vision__Media__RetentionPolicyVersion
  value: {{ $v.media.retentionPolicyVersion | quote }}
- name: Vision__Media__NormalizationPolicyVersion
  value: {{ $v.media.normalizationPolicyVersion | quote }}
- name: Vision__Media__IntentTtlMinutes
  value: {{ $v.media.intentTtlMinutes | quote }}
- name: Vision__Media__UnverifiedIntentGraceMinutes
  value: {{ $v.media.unverifiedIntentGraceMinutes | quote }}
- name: Vision__Media__SweeperIntervalMinutes
  value: {{ $v.media.sweeperIntervalMinutes | quote }}
- name: Vision__Media__SweeperBatchSize
  value: {{ $v.media.sweeperBatchSize | quote }}
{{- include "tranzrmoves.visionCatalogueLearningEnv" (dict "root" $root) | nindent 0 }}
{{- end }}

{{/*
Vision Video gates for the API. Emitted ONLY when features.vision.video is an
explicit map (shared values.yaml for staging + production). Omit the block → no
env lines, including no false defaults (byte-identical renders).
Usage: {{ include "tranzrmoves.visionVideoApiEnv" (dict "root" .) | nindent 12 }}
*/}}
{{- define "tranzrmoves.visionVideoApiEnv" -}}
{{- $root := .root -}}
{{- $vision := $root.Values.features.vision | default dict -}}
{{- if hasKey $vision "video" -}}
{{- $v := index $vision "video" | default dict -}}
- name: Vision__Video__IntakeEnabled
  value: {{ $v.intakeEnabled | quote }}
- name: Vision__Video__WorkerEnabled
  value: {{ $v.workerEnabled | quote }}
- name: Vision__Video__NativeCapabilityReady
  value: {{ $v.nativeCapabilityReady | quote }}
{{- end -}}
{{- end }}

{{/*
Vision Video gates for VisionNormalizer. Same explicit-block gate as API.
Intake stays false on the worker (API owns intake); Worker + Native follow values.
Usage: {{ include "tranzrmoves.visionVideoNormalizerEnv" (dict "root" .) | nindent 12 }}
*/}}
{{- define "tranzrmoves.visionVideoNormalizerEnv" -}}
{{- $root := .root -}}
{{- $vision := $root.Values.features.vision | default dict -}}
{{- if hasKey $vision "video" -}}
{{- $v := index $vision "video" | default dict -}}
- name: Vision__Video__IntakeEnabled
  value: "false"
- name: Vision__Video__WorkerEnabled
  value: {{ $v.workerEnabled | quote }}
- name: Vision__Video__NativeCapabilityReady
  value: {{ $v.nativeCapabilityReady | quote }}
{{- end -}}
{{- end }}

{{/*
Vision secrets for processor: Azure Blob + OpenRouter (Key Vault refs only).
Usage: {{ include "tranzrmoves.visionProcessorSecretEnv" (dict "root" .) | nindent 12 }}
*/}}
{{- define "tranzrmoves.visionProcessorSecretEnv" -}}
{{- $root := .root -}}
- name: AZURE_STORAGE_CONNECTION_STRING
  valueFrom:
    secretKeyRef:
      name: {{ $root.Values.externalSecrets.name }}
      key: tranzr-azure-storage-connection-string
- name: OPENROUTER_API_KEY
  valueFrom:
    secretKeyRef:
      name: {{ $root.Values.externalSecrets.name }}
      key: tranzr-openrouter-api-key
{{- end }}

{{/*
Vision secrets for scheduler: Azure Blob only (no OpenRouter).
Usage: {{ include "tranzrmoves.visionSchedulerSecretEnv" (dict "root" .) | nindent 12 }}
*/}}
{{- define "tranzrmoves.visionSchedulerSecretEnv" -}}
{{- $root := .root -}}
- name: AZURE_STORAGE_CONNECTION_STRING
  valueFrom:
    secretKeyRef:
      name: {{ $root.Values.externalSecrets.name }}
      key: tranzr-azure-storage-connection-string
{{- end }}

{{/*
Emit plain Seo__* env from a values map, skipping names already wired via
workerScheduler.additionalEnvFromSecrets. Duplicate env names (empty value +
valueFrom) break strategic merge / Argo CD ComparisonError.
Usage:
  {{ include "tranzrmoves.seoMapEnv" (dict "root" . "prefix" "Seo__DataForSEO__" "map" .Values.deployments.workerScheduler.seoDataForSeo) | nindent 12 }}
*/}}
{{- define "tranzrmoves.seoMapEnv" -}}
{{- $root := .root -}}
{{- $prefix := .prefix -}}
{{- $map := .map | default dict -}}
{{- $secretNames := dict -}}
{{- range ($root.Values.deployments.workerScheduler.additionalEnvFromSecrets | default list) }}
{{- $_ := set $secretNames .name true -}}
{{- end }}
{{- range $key, $val := $map }}
{{- $envName := printf "%s%s" $prefix $key -}}
{{- if not (hasKey $secretNames $envName) }}
- name: {{ $envName }}
  value: {{ $val | quote }}
{{- end }}
{{- end }}
{{- end }}
