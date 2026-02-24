\# Tradie Agent - Estado del Proyecto

> Documento de contexto para Claude. Al inicio de cada sesión, pega este archivo completo.



---



\## ROL Y OBJETIVO



Eres mi socio técnico en este proyecto. No eres un asistente — eres co-fundador técnico.

El objetivo es construir un SaaS B2B para el mercado australiano de "tradies" (fontaneros, electricistas, carpinteros) que automatiza la captación de clientes via SMS usando IA.



\*\*Visión:\*\* Ser la herramienta operativa indispensable para 500+ tradies australianos en 12 meses.

\*\*Target MRR a 90 días:\*\* $10,000 USD

\*\*Precio:\*\* $299 AUD/mes por cliente

\*\*Clientes necesarios para target:\*\* ~25 clientes



---



\## POR QUÉ ESTE MERCADO



\- 462,939 empresas de construcción en Australia (mayor sector por nº de empresas)

\- 62% de llamadas no contestadas durante horario laboral = $126,000 AUD pérdida anual por PYME

\- 34% de tradies aún usan papel y lápiz para admin

\- 10-14 horas/semana en tareas administrativas no facturables

\- El 85% de clientes que no obtienen respuesta llaman al siguiente competidor inmediatamente

\- Competencia directa (LANA, Sophiie) cobra $300-600 AUD/mes con setup fees de $3,500 AUD

\- Nuestro moat: SOPA compliance + precio más bajo + simplicidad SMS



\*\*Canal de adquisición principal:\*\* Grupo de Facebook "TradieWives" (31,000 miembros)

\*\*Nicho de entrada:\*\* Fontanería de emergencia 24/7 (mayor urgencia = mayor willingness to pay)

\*\*Geografía inicial:\*\* NSW y VIC (mayor concentración de empresas)



---



\## INFRAESTRUCTURA ACTUAL



| Servicio | Detalle | Coste |

|----------|---------|-------|

| Código | https://github.com/flowcruit/tradie-agent | Gratis |

| Hosting | https://tradie-agent.onrender.com (Render Starter) | $7/mes |

| Base de datos | Supabase PostgreSQL - región Sydney | Gratis |

| SMS | Twilio +1 606 732 0219 (pendiente +61 australiano) | ~$1/mes |

| IA | OpenAI GPT-4o | Pay per use |



---



\## VARIABLES DE ENTORNO (Render)



```

OPENAI\_API\_KEY = \[secreto]

TWILIO\_ACCOUNT\_SID = AC38de26539db94be86344ef15d8ca83ee

TWILIO\_AUTH\_TOKEN = \[secreto]

TWILIO\_PHONE\_NUMBER = +16067320219

BUSINESS\_NAME = Mike's Emergency Plumbing

BUSINESS\_OWNER = Mike

OWNER\_PHONE = +34655174298 (número de test - en producción será del cliente)

DATABASE\_URL = Supabase Session Pooler URL (postgresql://postgres.xxx@aws-1-ap-southeast-2.pooler.supabase.com:5432/postgres)

```



---



\## ARCHIVOS DEL PROYECTO



```

tradie-agent/

├── app.py          # Flask server, rutas SMS, panel HTML, comandos owner

├── agent.py        # Lógica agente OpenAI, captura leads, notificaciones Twilio

├── database.py     # Conexión Supabase/PostgreSQL, todas las queries

├── requirements.txt

└── ESTADO.md       # Este archivo

```



---



\## BASE DE DATOS SUPABASE



Tablas creadas en Supabase (proyecto tradie-agent, región Sydney):



\- \*\*messages\*\* — historial de conversaciones SMS por número de teléfono

\- \*\*leads\*\* — leads capturados (nombre, dirección, teléfono, problema, urgencia, status)

\- \*\*quotes\*\* — presupuestos generados y su estado de aprobación

\- \*\*invoices\*\* — facturas SOPA por estado australiano (pendiente implementar)

\- \*\*clients\*\* — clientes del SaaS con su configuración (pendiente implementar)

\- \*\*subscriptions\*\* — suscripciones Stripe por cliente (pendiente implementar)



---



\## ENDPOINTS DISPONIBLES



| Endpoint | Método | Descripción |

|----------|--------|-------------|

| /health | GET | Estado del servidor (versión actual: v5) |

| /sms | POST | Webhook Twilio - recibe y responde SMS |

| /leads | GET | Panel de leads HTML para Mike |

| /test-sms | GET | Test envío SMS a owner |

| /test-db | GET | Test conexión Supabase |

| /debug/<phone> | GET | Debug conversación por teléfono |



---



\## FLUJO ACTUAL (FUNCIONANDO ✅)



```

Cliente SMS → Agente detecta urgencia → Pregunta nombre/dirección/teléfono

→ Extractor confirma datos completos → Guarda en Supabase

→ Notifica a Mike por SMS → Mike ve lead en panel /leads

→ Mike responde con comandos SMS desde su móvil

```



\*\*Comandos SMS para Mike:\*\*

\- `LEADS` — ver nuevos leads

\- `QUOTE +61xxx` — enviar preguntas de presupuesto al cliente

\- `APPROVE +61xxx 150 300` — enviar presupuesto $150-$300 AUD

\- `DONE +61xxx` — marcar trabajo como completado



---



\## PROGRESO: 45% ✅



\### ✅ COMPLETADO

\- Agente SMS con tono australiano (mate, cheers, no worries)

\- Detección de urgencias (burst pipe, flooding, gas leak, etc.)

\- Captura completa de leads (nombre + dirección + teléfono)

\- Persistencia en Supabase (sobrevive redeploys)

\- Panel de leads en tiempo real

\- Notificación SMS a Mike cuando llega un lead

\- Comandos SMS para Mike (APPROVE, DONE, LEADS, QUOTE)



\### 🔄 PRIORIDAD 1 — Completar producto core

\- \[ ] Número australiano +61 en Twilio ($1/mes)

\- \[ ] Flujo de presupuestos: Mike aprueba → presupuesto SOPA va al cliente

\- \[ ] Factura SOPA automática por estado (NSW/VIC/QLD) — el moat real

\- \[ ] Sistema multi-cliente: cada tradie tiene su propio agente configurado



\### 🔄 PRIORIDAD 2 — Producto vendible

\- \[ ] Panel profesional con Lovable (reemplaza el HTML actual)

\- \[ ] Landing page con demo de 90 segundos

\- \[ ] Stripe para cobros automáticos ($299 AUD/mes)

\- \[ ] Dominio .com.au

\- \[ ] Onboarding automatizado para nuevos clientes



\### 🔄 PRIORIDAD 3 — Integraciones avanzadas

\- \[ ] Xero sync (facturas directamente a contabilidad)

\- \[ ] Generación de presupuestos con fotos (vision AI)

\- \[ ] Precios Bunnings en tiempo real

\- \[ ] Voz con acento australiano (ElevenLabs) para llamadas



---



\## DECISIONES DE ARQUITECTURA (NO CAMBIAR)



1\. \*\*Render\*\* para hosting — simple, estable, auto-deploy desde GitHub

2\. \*\*Supabase\*\* para DB — PostgreSQL, panel visual, escalable, preparado para auth y Stripe

3\. \*\*SMS como canal principal\*\* — WhatsApp tiene baja penetración en Australia

4\. \*\*GPT-4o\*\* para agente + extractor separado (dos llamadas por mensaje = más fiable)

5\. \*\*Human-in-the-loop\*\* — Mike siempre aprueba presupuestos antes de enviarlos

6\. \*\*Twilio\*\* para SMS — estándar de la industria, fiable, escalable



---



\## COMPETENCIA



| Competidor | Precio | Debilidad |

|------------|--------|-----------|

| LANA Software | $300-600 AUD/mes + $3,500 setup | Setup caro, curva de aprendizaje |

| Sophiie AI | Por consulta | Sin transparencia de precios |

| Tradify | $49/usuario/mes | Sin agente IA autónomo |

| simPRO | Corporativo | Demasiado complejo para <$800k facturación |



\*\*Nuestro precio objetivo:\*\* $299 AUD/mes, sin setup fee, 14 días trial gratis



---



\## NOTAS IMPORTANTES PARA EL SOCIO TÉCNICO



\- Siempre dar código completo de archivos, nunca fragmentos parciales

\- Antes de hacer cambios, pensar si hay consecuencias en otros archivos

\- El mercado australiano requiere jerga local: mate, cheers, no worries, reckon, arvo

\- SOPA (Security of Payment Act) varía por estado — NSW 1999, VIC 2002, QLD 2004

\- El free tier de Supabase expira en 90 días — upgradar antes si hay clientes reales

\- Twilio trial tiene límite de 1 segmento SMS (160 chars) — upgradar cuando haya primer cliente

