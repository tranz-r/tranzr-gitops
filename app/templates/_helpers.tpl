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
