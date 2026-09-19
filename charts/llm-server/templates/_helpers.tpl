{{- define "llm.labels" -}}
app.kubernetes.io/name: llm-server
app.kubernetes.io/instance: {{ .Values.name }}
app.kubernetes.io/component: {{ .Values.engine }}
{{- end }}
{{- define "llm.selector" -}}
app.kubernetes.io/name: llm-server
app.kubernetes.io/instance: {{ .Values.name }}
{{- end }}
