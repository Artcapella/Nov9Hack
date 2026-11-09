{{- define "network-concierge.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- define "network-concierge.fullname" -}}
{{- printf "%s-%s" .Release.Name (include "network-concierge.name" .) | trunc 63 | trimSuffix "-" }}
{{- end }}
