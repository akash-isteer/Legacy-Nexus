"""Reference data used by the engine: capability taxonomy and modernization target catalogs.

These are data tables, kept in one place so teams can extend them for their own estate.
"""

# Ordered: the first keyword found in a program's id, then its tables, then its purpose wins.
CAPABILITY_KEYWORDS: list[tuple[tuple[str, ...], str]] = [
    (("CUST", "CLIENT", "PARTY", "KYC", "CIF"), "Customer Management"),
    (("ACCT", "ACCOUNT", "DEPOSIT", "LEDGERBAL"), "Account Processing"),
    (("INTEREST", "BILL", "FEE", "CHARGE", "INVOICE", "RATE"), "Interest & Billing"),
    (("PAY", "PAYMENT", "TRANSFER", "REMIT", "SWIFT", "ACH", "WIRE"), "Payments"),
    (("LOAN", "MORTGAGE", "CREDIT", "LEND"), "Lending"),
    (("CARD", "AUTH", "CHARGEBACK"), "Cards"),
    (("GL", "LEDGER", "JOURNAL", "POSTING"), "General Ledger"),
    (("CLAIM",), "Claims"),
    (("POLICY", "PREMIUM", "UNDERWRIT"), "Policy Administration"),
    (("ORDER", "SHIP", "INVENTORY", "STOCK"), "Order Management"),
    (("HR", "PAYROLL", "EMPLOYEE", "SALARY"), "Payroll & HR"),
    (("RPT", "REPORT", "STMT", "STATEMENT", "EXTRACT"), "Reporting"),
    (("ARCHIV", "PURGE", "HOUSEKEEP", "TELEX", "AUDIT"), "Archiving & Housekeeping"),
]

TARGET_LANGUAGES = {
    "java": {
        "label": "Java 21 + Spring Boot 3",
        "service": "Spring Boot microservice",
        "batch": "Spring Batch job",
        "program": "@Service class",
        "paragraph": "private method",
        "copybook": "Java record / DTO",
        "packed_decimal": "java.math.BigDecimal",
        "data_access": "Spring Data JPA repository",
        "testing": "JUnit 5 + Testcontainers",
    },
    "dotnet": {
        "label": ".NET 8 + ASP.NET Core",
        "service": "ASP.NET Core minimal API service",
        "batch": ".NET Worker Service (Hangfire / Quartz.NET)",
        "program": "C# service class",
        "paragraph": "private method",
        "copybook": "C# record type",
        "packed_decimal": "System.Decimal",
        "data_access": "Entity Framework Core DbContext",
        "testing": "xUnit + Testcontainers",
    },
    "python": {
        "label": "Python 3.12 + FastAPI",
        "service": "FastAPI microservice",
        "batch": "Python batch worker (Celery / Airflow task)",
        "program": "service module",
        "paragraph": "function",
        "copybook": "Pydantic model",
        "packed_decimal": "decimal.Decimal",
        "data_access": "SQLAlchemy repository",
        "testing": "pytest + Testcontainers",
    },
}

TARGET_DATABASES = {
    "postgresql": {"label": "PostgreSQL 16", "sqlglot_dialect": "postgres"},
    "azure_sql": {"label": "Azure SQL Database", "sqlglot_dialect": "tsql"},
    "oracle": {"label": "Oracle Database 23ai", "sqlglot_dialect": "oracle"},
    "mysql": {"label": "MySQL 8", "sqlglot_dialect": "mysql"},
}

TARGET_PLATFORMS = {
    "azure": {
        "label": "Microsoft Azure",
        "compute": "Azure Kubernetes Service (AKS) / Azure Container Apps",
        "batch": "Azure Batch or Spring Batch on AKS, scheduled by Azure Logic Apps",
        "api_gateway": "Azure API Management",
        "messaging": "Azure Service Bus",
        "files": "Azure Blob Storage",
        "secrets": "Azure Key Vault",
        "observability": "Azure Monitor + Application Insights",
        "database_hosting": {"postgresql": "Azure Database for PostgreSQL – Flexible Server",
                             "azure_sql": "Azure SQL Database", "oracle": "Oracle Database@Azure",
                             "mysql": "Azure Database for MySQL"},
    },
    "aws": {
        "label": "Amazon Web Services",
        "compute": "Amazon EKS / AWS Fargate",
        "batch": "AWS Batch or Spring Batch on EKS, scheduled by Amazon EventBridge",
        "api_gateway": "Amazon API Gateway",
        "messaging": "Amazon SQS / Amazon MQ",
        "files": "Amazon S3",
        "secrets": "AWS Secrets Manager",
        "observability": "Amazon CloudWatch + AWS X-Ray",
        "database_hosting": {"postgresql": "Amazon Aurora PostgreSQL", "azure_sql": "Amazon RDS for SQL Server",
                             "oracle": "Amazon RDS for Oracle", "mysql": "Amazon Aurora MySQL"},
    },
    "gcp": {
        "label": "Google Cloud",
        "compute": "Google Kubernetes Engine (GKE) / Cloud Run",
        "batch": "Google Cloud Batch, scheduled by Cloud Scheduler",
        "api_gateway": "Apigee API Management",
        "messaging": "Google Cloud Pub/Sub",
        "files": "Google Cloud Storage",
        "secrets": "Google Secret Manager",
        "observability": "Google Cloud Operations Suite",
        "database_hosting": {"postgresql": "Cloud SQL for PostgreSQL / AlloyDB", "azure_sql": "Cloud SQL for SQL Server",
                             "oracle": "Oracle Database@Google Cloud", "mysql": "Cloud SQL for MySQL"},
    },
    "on_prem": {
        "label": "On-premises Kubernetes",
        "compute": "Red Hat OpenShift / Kubernetes",
        "batch": "Kubernetes CronJobs running batch containers",
        "api_gateway": "Kong / NGINX API gateway",
        "messaging": "IBM MQ or Apache Kafka",
        "files": "S3-compatible object storage (MinIO)",
        "secrets": "HashiCorp Vault",
        "observability": "Prometheus + Grafana + OpenTelemetry",
        "database_hosting": {"postgresql": "PostgreSQL (operator-managed)", "azure_sql": "SQL Server on Linux",
                             "oracle": "Oracle Database", "mysql": "MySQL (operator-managed)"},
    },
}

KNOWN_EXTERNAL_INTERFACES = {
    "MQPUT": "IBM MQ – put message", "MQGET": "IBM MQ – get message", "MQOPEN": "IBM MQ – open queue",
    "MQCONN": "IBM MQ – connect", "MQPUT1": "IBM MQ – put single message",
    "CEEDAYS": "LE date service", "CEEDATE": "LE date service", "CEELOCT": "LE local time service",
    "DSNTIAR": "DB2 error formatter", "CBLTDLI": "IMS DL/I call", "AIBTDLI": "IMS DL/I call",
}
