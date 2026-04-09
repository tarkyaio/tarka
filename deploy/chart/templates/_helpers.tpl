{{/*
Expand the name of the chart.
*/}}
{{- define "tarka.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
We truncate at 63 chars because some Kubernetes name fields are limited to this (by the DNS naming spec).
If release name contains chart name it will be used as a full name.
*/}}
{{- define "tarka.fullname" -}}
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
{{- define "tarka.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "tarka.labels" -}}
helm.sh/chart: {{ include "tarka.chart" . }}
{{ include "tarka.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels (base)
*/}}
{{- define "tarka.selectorLabels" -}}
app.kubernetes.io/name: {{ include "tarka.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/* ====================================================================
     Component fullnames
     ==================================================================== */}}

{{- define "tarka.webhook.fullname" -}}
{{- printf "%s-webhook" (include "tarka.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "tarka.worker.fullname" -}}
{{- printf "%s-worker" (include "tarka.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "tarka.ui.fullname" -}}
{{- printf "%s-ui" (include "tarka.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/* ====================================================================
     Component selector labels
     ==================================================================== */}}

{{- define "tarka.webhook.selectorLabels" -}}
{{ include "tarka.selectorLabels" . }}
app.kubernetes.io/component: webhook
{{- end }}

{{- define "tarka.worker.selectorLabels" -}}
{{ include "tarka.selectorLabels" . }}
app.kubernetes.io/component: worker
{{- end }}

{{- define "tarka.ui.selectorLabels" -}}
{{ include "tarka.selectorLabels" . }}
app.kubernetes.io/component: ui
{{- end }}

{{/* ====================================================================
     Resource name helpers
     ==================================================================== */}}

{{/*
ConfigMap name: use existing or generate.
*/}}
{{- define "tarka.configMapName" -}}
{{- if .Values.config.existingConfigMap }}
{{- .Values.config.existingConfigMap }}
{{- else }}
{{- printf "%s-config" (include "tarka.fullname" .) }}
{{- end }}
{{- end }}

{{/*
Secret name: use existingSecret if set, otherwise generate.
Works for both static (chart-created Secret) and external (ESO-created Secret).
*/}}
{{- define "tarka.secretName" -}}
{{- if .Values.secrets.existingSecret }}
{{- .Values.secrets.existingSecret }}
{{- else }}
{{- include "tarka.fullname" . }}
{{- end }}
{{- end }}

{{/*
ServiceAccount name
*/}}
{{- define "tarka.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "tarka.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Image tag: default to Chart.appVersion
*/}}
{{- define "tarka.imageTag" -}}
{{- default .Chart.AppVersion .Values.image.tag }}
{{- end }}

{{- define "tarka.ui.imageTag" -}}
{{- default .Chart.AppVersion .Values.ui.image.tag }}
{{- end }}

{{/* ====================================================================
     Shared env vars from Secret (used by both webhook and worker)
     All refs are optional: true so missing keys don't block startup.
     ==================================================================== */}}

{{- define "tarka.secretEnvVars" -}}
- name: POSTGRES_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ include "tarka.secretName" . }}
      key: POSTGRES_PASSWORD
      optional: true
- name: POSTGRES_DSN
  valueFrom:
    secretKeyRef:
      name: {{ include "tarka.secretName" . }}
      key: POSTGRES_DSN
      optional: true
- name: LANGSMITH_API_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "tarka.secretName" . }}
      key: LANGSMITH_API_KEY
      optional: true
- name: ANTHROPIC_API_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "tarka.secretName" . }}
      key: ANTHROPIC_API_KEY
      optional: true
- name: GITHUB_APP_ID
  valueFrom:
    secretKeyRef:
      name: {{ include "tarka.secretName" . }}
      key: GITHUB_APP_ID
      optional: true
- name: GITHUB_APP_PRIVATE_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "tarka.secretName" . }}
      key: GITHUB_APP_PRIVATE_KEY
      optional: true
- name: GITHUB_APP_INSTALLATION_ID
  valueFrom:
    secretKeyRef:
      name: {{ include "tarka.secretName" . }}
      key: GITHUB_APP_INSTALLATION_ID
      optional: true
- name: GOOGLE_OAUTH_CLIENT_ID
  valueFrom:
    secretKeyRef:
      name: {{ include "tarka.secretName" . }}
      key: GOOGLE_OAUTH_CLIENT_ID
      optional: true
- name: GOOGLE_OAUTH_CLIENT_SECRET
  valueFrom:
    secretKeyRef:
      name: {{ include "tarka.secretName" . }}
      key: GOOGLE_OAUTH_CLIENT_SECRET
      optional: true
- name: AUTH_SESSION_SECRET
  valueFrom:
    secretKeyRef:
      name: {{ include "tarka.secretName" . }}
      key: AUTH_SESSION_SECRET
      optional: true
- name: ADMIN_INITIAL_USERNAME
  valueFrom:
    secretKeyRef:
      name: {{ include "tarka.secretName" . }}
      key: ADMIN_INITIAL_USERNAME
      optional: true
- name: ADMIN_INITIAL_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ include "tarka.secretName" . }}
      key: ADMIN_INITIAL_PASSWORD
      optional: true
- name: OIDC_DISCOVERY_URL
  valueFrom:
    secretKeyRef:
      name: {{ include "tarka.secretName" . }}
      key: OIDC_DISCOVERY_URL
      optional: true
- name: OIDC_CLIENT_ID
  valueFrom:
    secretKeyRef:
      name: {{ include "tarka.secretName" . }}
      key: OIDC_CLIENT_ID
      optional: true
- name: OIDC_CLIENT_SECRET
  valueFrom:
    secretKeyRef:
      name: {{ include "tarka.secretName" . }}
      key: OIDC_CLIENT_SECRET
      optional: true
- name: SLACK_BOT_TOKEN
  valueFrom:
    secretKeyRef:
      name: {{ include "tarka.secretName" . }}
      key: SLACK_BOT_TOKEN
      optional: true
- name: SLACK_APP_TOKEN
  valueFrom:
    secretKeyRef:
      name: {{ include "tarka.secretName" . }}
      key: SLACK_APP_TOKEN
      optional: true
- name: SLACK_SIGNING_SECRET
  valueFrom:
    secretKeyRef:
      name: {{ include "tarka.secretName" . }}
      key: SLACK_SIGNING_SECRET
      optional: true
- name: CONSOLE_AUTH_USERNAME
  valueFrom:
    secretKeyRef:
      name: {{ include "tarka.secretName" . }}
      key: CONSOLE_AUTH_USERNAME
      optional: true
- name: CONSOLE_AUTH_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ include "tarka.secretName" . }}
      key: CONSOLE_AUTH_PASSWORD
      optional: true
{{- end }}

{{/* ====================================================================
     Shared volumes (service catalog ConfigMap)
     ==================================================================== */}}

{{- define "tarka.sharedVolumes" -}}
{{- if .Values.serviceCatalog.enabled }}
- name: service-catalog
  configMap:
    name: {{ include "tarka.fullname" . }}-service-catalog
{{- end }}
{{- end }}

{{/* ====================================================================
     Shared volume mounts (service catalog)
     ==================================================================== */}}

{{- define "tarka.sharedVolumeMounts" -}}
{{- if .Values.serviceCatalog.enabled }}
- name: service-catalog
  mountPath: /app/config
  readOnly: true
{{- end }}
{{- end }}
