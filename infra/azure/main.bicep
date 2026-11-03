// LegacyNexus AI on Azure (DEPLOYMENT_PROFILE=cloud)
//
//   web (Container App, public)  ->  api (Container App, internal)  ->  PostgreSQL Flexible Server (+pgvector)
//                                          |                              database legacynexus        (platform + Code Q&A)
//                                          |                              database legacynexus_docqa  (Document Q&A)
//                                          |                        ->  Azure Files share (/app/data)
//                                          |                        ->  Azure OpenAI (optional: chat + embedding deployments)
//                                          v
//                                  Service Bus queue  ->  worker (Container Apps Job, scales 0..N on queue length)
//
// Secrets live in Key Vault and reach the apps through a user-assigned managed identity.
// Deploy with infra/azure/deploy.ps1 (builds the images in ACR, then rolls them out).

targetScope = 'resourceGroup'

@description('Short name used as a prefix for every resource, e.g. lnx')
@minLength(3)
@maxLength(10)
param namePrefix string = 'lnx'

@description('Environment label appended to names, e.g. dev, test, prod')
param env string = 'prod'

param location string = resourceGroup().location

@description('Container images. The first deployment can use the placeholder; deploy.ps1 swaps in the real images.')
param apiImage string = 'mcr.microsoft.com/k8se/quickstart:latest'
param webImage string = 'mcr.microsoft.com/k8se/quickstart:latest'

@description('PostgreSQL administrator login')
param dbAdminUser string = 'lnxadmin'

@secure()
@description('PostgreSQL administrator password (stored in Key Vault)')
param dbAdminPassword string

@secure()
@description('Application SECRET_KEY (min 32 chars). Leave empty on later deployments to keep the stored value.')
param appSecretKey string = ''

@description('First administrator account, created on first start')
param bootstrapAdminEmail string

@secure()
@description('First administrator password. Leave empty on later deployments to keep the stored value.')
param bootstrapAdminPassword string = ''

@description('Object id of the person or pipeline running the deployment; gets Key Vault Secrets Officer')
param deployerPrincipalId string = ''

@allowed(['User', 'ServicePrincipal'])
param deployerPrincipalType string = 'User'

@description('Allow self sign-up (false = invite-only)')
param allowSignup bool = false

@description('PostgreSQL compute size')
param dbSkuName string = 'Standard_B2s'
@allowed(['Burstable', 'GeneralPurpose', 'MemoryOptimized'])
param dbSkuTier string = 'Burstable'
param dbStorageGb int = 64

param apiMinReplicas int = 1
param apiMaxReplicas int = 5
param workerMaxExecutions int = 10

@description('free = smallest sizes that fit Azure free-account grants (PostgreSQL B1ms + 32 GB, apps scale to zero, Service Bus Basic). standard = the sizes above.')
@allowed(['free', 'standard'])
param sizing string = 'standard'

@description('Create an Azure OpenAI account with a chat and an embedding deployment, and configure the app to use them')
param deployOpenAI bool = false
@description('Azure OpenAI region (model availability differs by region)')
param openAILocation string = location
param openAIChatModel string = 'gpt-4o-mini'
param openAIChatVersion string = '2024-07-18'
param openAIEmbeddingModel string = 'text-embedding-3-small'
param openAIEmbeddingVersion string = '1'
@description('Thousands of tokens per minute for each deployment')
param openAICapacity int = 30
param openAIApiVersion string = '2024-10-21'

var isFree = sizing == 'free'
var effectiveDbSku = isFree ? 'Standard_B1ms' : dbSkuName
var effectiveDbTier = isFree ? 'Burstable' : dbSkuTier
var effectiveDbStorage = isFree ? 32 : dbStorageGb
var effectiveApiMin = isFree ? 0 : apiMinReplicas
var docqaDbName = 'legacynexus_docqa'

var base = toLower('${namePrefix}-${env}')
var compact = toLower(replace('${namePrefix}${env}', '-', ''))
var suffix = uniqueString(resourceGroup().id, base)
var queueName = 'analysis-jobs'
var apiName = '${base}-api'
var webName = '${base}-web'

// Built-in role definition ids
var roleKeyVaultSecretsUser = '4633458b-17de-408a-b874-0445c86b69e6'
var roleKeyVaultSecretsOfficer = 'b86a8fe4-44ce-4948-aee5-eccb2c155cd7'
var roleAcrPull = '7f951dda-4ed3-4680-a7ca-43fe172d538d'
var roleServiceBusSender = '69a216fc-b8fb-44d8-bc22-1f3c2cd27a39'
var roleServiceBusReceiver = '4f6d3b9b-027b-4f4c-9142-0e5a2a2247e0'

// ---------------------------------------------------------------- identity, monitoring, registry, secrets

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${base}-id'
  location: location
}

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${base}-logs'
  location: location
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: take('${compact}acr${suffix}', 50)
  location: location
  sku: { name: 'Basic' }
  properties: { adminUserEnabled: false }
}

resource acrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, identity.id, roleAcrPull)
  scope: acr
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleAcrPull)
  }
}

resource vault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: take('${compact}kv${suffix}', 24)
  location: location
  properties: {
    tenantId: subscription().tenantId
    sku: { family: 'A', name: 'standard' }
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 30
  }
}

resource vaultReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(vault.id, identity.id, roleKeyVaultSecretsUser)
  scope: vault
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleKeyVaultSecretsUser)
  }
}

resource vaultOfficer 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(deployerPrincipalId)) {
  name: guid(vault.id, deployerPrincipalId, roleKeyVaultSecretsOfficer)
  scope: vault
  properties: {
    principalId: deployerPrincipalId
    principalType: deployerPrincipalType
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleKeyVaultSecretsOfficer)
  }
}

// Secrets are written only when a value is passed, so redeployments never rotate them by accident
// (rotating SECRET_KEY would make stored API keys unreadable). Apps reference the latest version.
resource secretKey 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = if (!empty(appSecretKey)) {
  parent: vault
  name: 'secret-key'
  properties: { value: appSecretKey }
}

resource dbPassword 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: vault
  name: 'db-password'
  properties: { value: dbAdminPassword }
}

resource adminPassword 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = if (!empty(bootstrapAdminPassword)) {
  parent: vault
  name: 'bootstrap-admin-password'
  properties: { value: bootstrapAdminPassword }
}

var vaultSecretUri = '${vault.properties.vaultUri}secrets/'

// ---------------------------------------------------------------- PostgreSQL with pgvector

resource postgres 'Microsoft.DBforPostgreSQL/flexibleServers@2023-12-01-preview' = {
  name: '${base}-pg-${suffix}'
  location: location
  sku: {
    name: effectiveDbSku
    tier: effectiveDbTier
  }
  properties: {
    version: '16'
    administratorLogin: dbAdminUser
    administratorLoginPassword: dbAdminPassword
    storage: { storageSizeGB: effectiveDbStorage, autoGrow: isFree ? 'Disabled' : 'Enabled' }
    backup: { backupRetentionDays: 14, geoRedundantBackup: 'Disabled' }
    highAvailability: { mode: 'Disabled' }
    network: { publicNetworkAccess: 'Enabled' }
  }
}

resource pgAllowAzure 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2023-12-01-preview' = {
  parent: postgres
  name: 'allow-azure-services'
  properties: { startIpAddress: '0.0.0.0', endIpAddress: '0.0.0.0' }
}

resource pgVector 'Microsoft.DBforPostgreSQL/flexibleServers/configurations@2023-12-01-preview' = {
  parent: postgres
  name: 'azure.extensions'
  properties: { value: 'VECTOR', source: 'user-override' }
}

resource database 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2023-12-01-preview' = {
  parent: postgres
  name: 'legacynexus'
  dependsOn: [pgVector]
  properties: { charset: 'UTF8', collation: 'en_US.utf8' }
}

// Document Q&A keeps its data, conversations and vectors in its own database, never mixed with Code Q&A.
resource docqaDatabase 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2023-12-01-preview' = {
  parent: postgres
  name: docqaDbName
  dependsOn: [pgVector, database]
  properties: { charset: 'UTF8', collation: 'en_US.utf8' }
}

// ---------------------------------------------------------------- Azure OpenAI (optional)

resource openai 'Microsoft.CognitiveServices/accounts@2024-10-01' = if (deployOpenAI) {
  name: take('${compact}oai${suffix}', 64)
  location: openAILocation
  kind: 'OpenAI'
  sku: { name: 'S0' }
  properties: {
    customSubDomainName: take('${compact}oai${suffix}', 64)
    publicNetworkAccess: 'Enabled'
  }
}

resource chatDeployment 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = if (deployOpenAI) {
  parent: openai
  name: openAIChatModel
  sku: { name: 'GlobalStandard', capacity: openAICapacity }
  properties: { model: { format: 'OpenAI', name: openAIChatModel, version: openAIChatVersion } }
}

resource embeddingDeployment 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = if (deployOpenAI) {
  parent: openai
  name: openAIEmbeddingModel
  dependsOn: [chatDeployment] // one deployment at a time per account
  sku: { name: 'Standard', capacity: openAICapacity }
  properties: { model: { format: 'OpenAI', name: openAIEmbeddingModel, version: openAIEmbeddingVersion } }
}

resource aiKey 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = if (deployOpenAI) {
  parent: vault
  name: 'ai-api-key'
  properties: { value: deployOpenAI ? openai!.listKeys().key1 : '' }
}

// ---------------------------------------------------------------- Service Bus

resource bus 'Microsoft.ServiceBus/namespaces@2022-10-01-preview' = {
  name: '${base}-sb-${suffix}'
  location: location
  sku: { name: isFree ? 'Basic' : 'Standard', tier: isFree ? 'Basic' : 'Standard' }
}

resource queue 'Microsoft.ServiceBus/namespaces/queues@2022-10-01-preview' = {
  parent: bus
  name: queueName
  properties: {
    lockDuration: 'PT5M'
    maxDeliveryCount: 5
    deadLetteringOnMessageExpiration: true
    defaultMessageTimeToLive: 'P7D'
  }
}

resource kedaListen 'Microsoft.ServiceBus/namespaces/queues/authorizationRules@2022-10-01-preview' = {
  parent: queue
  name: 'keda-scaler'
  properties: { rights: ['Listen'] }
}

resource busSender 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(bus.id, identity.id, roleServiceBusSender)
  scope: bus
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleServiceBusSender)
  }
}

resource busReceiver 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(bus.id, identity.id, roleServiceBusReceiver)
  scope: bus
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleServiceBusReceiver)
  }
}

// ---------------------------------------------------------------- file share for uploads and caches

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: take('${compact}st${suffix}', 24)
  location: location
  kind: 'StorageV2'
  sku: { name: 'Standard_LRS' }
  properties: {
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    supportsHttpsTrafficOnly: true
  }
}

resource fileService 'Microsoft.Storage/storageAccounts/fileServices@2023-05-01' = {
  parent: storage
  name: 'default'
}

resource share 'Microsoft.Storage/storageAccounts/fileServices/shares@2023-05-01' = {
  parent: fileService
  name: 'legacynexus-data'
  properties: { shareQuota: 1024 }
}

// ---------------------------------------------------------------- Container Apps

resource appEnv 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: '${base}-env'
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
    }
  }
}

resource envStorage 'Microsoft.App/managedEnvironments/storages@2024-03-01' = {
  parent: appEnv
  name: 'data'
  properties: {
    azureFile: {
      accountName: storage.name
      accountKey: storage.listKeys().keys[0].value
      shareName: share.name
      accessMode: 'ReadWrite'
    }
  }
}

var webFqdn = '${webName}.${appEnv.properties.defaultDomain}'
var apiInternalUrl = 'https://${apiName}.internal.${appEnv.properties.defaultDomain}'

var kvSecrets = concat([
  { name: 'secret-key', keyVaultUrl: '${vaultSecretUri}secret-key', identity: identity.id }
  { name: 'db-password', keyVaultUrl: '${vaultSecretUri}db-password', identity: identity.id }
  { name: 'bootstrap-admin-password', keyVaultUrl: '${vaultSecretUri}bootstrap-admin-password', identity: identity.id }
], deployOpenAI ? [
  { name: 'ai-api-key', keyVaultUrl: '${vaultSecretUri}ai-api-key', identity: identity.id }
] : [])

// Azure OpenAI wiring: applied by the app on first start while Settings has no model (admins can change it later).
var openAIEndpoint = deployOpenAI ? openai!.properties.endpoint : ''
var openAIEnv = deployOpenAI ? [
  { name: 'AI_PROVIDER', value: 'azure_openai' }
  { name: 'AI_BASE_URL', value: openAIEndpoint }
  { name: 'AI_DEPLOYMENT', value: openAIChatModel }
  { name: 'AI_API_VERSION', value: openAIApiVersion }
  { name: 'AI_API_KEY', secretRef: 'ai-api-key' }
  { name: 'EMBEDDING_PROVIDER', value: 'azure_openai' }
  { name: 'EMBEDDING_BASE_URL', value: openAIEndpoint }
  { name: 'EMBEDDING_DEPLOYMENT', value: openAIEmbeddingModel }
  { name: 'EMBEDDING_API_VERSION', value: openAIApiVersion }
] : []

var backendEnv = concat([
  { name: 'DEPLOYMENT_PROFILE', value: 'cloud' }
  { name: 'ENVIRONMENT', value: 'production' }
  { name: 'SECRET_KEY', secretRef: 'secret-key' }
  { name: 'DB_HOST', value: postgres.properties.fullyQualifiedDomainName }
  { name: 'DB_NAME', value: database.name }
  { name: 'DB_USER', value: dbAdminUser }
  { name: 'DB_PASSWORD', secretRef: 'db-password' }
  { name: 'DB_SSLMODE', value: 'require' }
  { name: 'VECTOR_STORE', value: 'pgvector' }
  { name: 'DOCQA_DB_NAME', value: docqaDatabase.name }
  { name: 'DOCQA_VECTOR_STORE', value: 'pgvector' }
  { name: 'CONVERSATION_CONTEXT', value: 'auto' }
  { name: 'STORAGE_DIR', value: '/app/data/storage' }
  { name: 'JOB_BACKEND', value: 'servicebus' }
  { name: 'SERVICEBUS_NAMESPACE', value: '${bus.name}.servicebus.windows.net' }
  { name: 'SERVICEBUS_QUEUE', value: queueName }
  { name: 'AZURE_CLIENT_ID', value: identity.properties.clientId }
  { name: 'CORS_ORIGINS', value: 'https://${webFqdn}' }
  { name: 'APP_BASE_URL', value: 'https://${webFqdn}' }
  { name: 'ALLOW_SIGNUP', value: string(allowSignup) }
  { name: 'BOOTSTRAP_ADMIN_EMAIL', value: bootstrapAdminEmail }
  { name: 'BOOTSTRAP_ADMIN_PASSWORD', secretRef: 'bootstrap-admin-password' }
], openAIEnv)

var dataVolume = [{ name: 'data', storageType: 'AzureFile', storageName: envStorage.name, mountOptions: 'dir_mode=0777,file_mode=0777' }]
var dataMount = [{ volumeName: 'data', mountPath: '/app/data' }]
var registry = [{ server: acr.properties.loginServer, identity: identity.id }]

resource api 'Microsoft.App/containerApps@2024-03-01' = {
  name: apiName
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${identity.id}': {} }
  }
  dependsOn: [vaultReader, acrPull, busSender, pgAllowAzure, secretKey, dbPassword, adminPassword, aiKey]
  properties: {
    managedEnvironmentId: appEnv.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: false
        targetPort: 8000
        transport: 'auto'
      }
      registries: registry
      secrets: kvSecrets
    }
    template: {
      containers: [
        {
          name: 'api'
          image: apiImage
          env: backendEnv
          resources: { cpu: json('1.0'), memory: '2Gi' }
          volumeMounts: dataMount
          probes: [
            { type: 'Liveness', httpGet: { path: '/api/health', port: 8000 }, periodSeconds: 30 }
            { type: 'Readiness', httpGet: { path: '/api/health/ready', port: 8000 }, periodSeconds: 30, failureThreshold: 6 }
          ]
        }
      ]
      volumes: dataVolume
      scale: {
        minReplicas: effectiveApiMin
        maxReplicas: apiMaxReplicas
        rules: [{ name: 'http', http: { metadata: { concurrentRequests: '50' } } }]
      }
    }
  }
}

resource worker 'Microsoft.App/jobs@2024-03-01' = {
  name: '${base}-worker'
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${identity.id}': {} }
  }
  dependsOn: [vaultReader, acrPull, busReceiver, secretKey, dbPassword, adminPassword, aiKey]
  properties: {
    environmentId: appEnv.id
    configuration: {
      triggerType: 'Event'
      replicaTimeout: 21600      // large estates: up to 6 hours per analysis
      replicaRetryLimit: 1
      registries: registry
      secrets: concat(kvSecrets, [
        { name: 'sb-listen', value: kedaListen.listKeys().primaryConnectionString }
      ])
      eventTriggerConfig: {
        parallelism: 1
        replicaCompletionCount: 1
        scale: {
          minExecutions: 0
          maxExecutions: workerMaxExecutions
          pollingInterval: 20
          rules: [
            {
              name: 'analysis-queue'
              type: 'azure-servicebus'
              metadata: { queueName: queueName, messageCount: '1' }
              auth: [{ secretRef: 'sb-listen', triggerParameter: 'connection' }]
            }
          ]
        }
      }
    }
    template: {
      containers: [
        {
          name: 'worker'
          image: apiImage
          command: ['python', '-m', 'app.worker']
          env: concat(backendEnv, [{ name: 'WORKER_RUN_ONCE', value: 'true' }])
          resources: { cpu: json('2.0'), memory: '4Gi' }
          volumeMounts: dataMount
        }
      ]
      volumes: dataVolume
    }
  }
}

resource web 'Microsoft.App/containerApps@2024-03-01' = {
  name: webName
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${identity.id}': {} }
  }
  dependsOn: [acrPull]
  properties: {
    managedEnvironmentId: appEnv.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 80
        transport: 'auto'
        allowInsecure: false
      }
      registries: registry
    }
    template: {
      containers: [
        {
          name: 'web'
          image: webImage
          env: [{ name: 'BACKEND_URL', value: apiInternalUrl }]
          resources: { cpu: json('0.5'), memory: '1Gi' }
          probes: [{ type: 'Liveness', httpGet: { path: '/healthz', port: 80 }, periodSeconds: 30 }]
        }
      ]
      scale: { minReplicas: isFree ? 0 : 1, maxReplicas: 3 }
    }
  }
}

output webUrl string = 'https://${web.properties.configuration.ingress.fqdn}'
output acrName string = acr.name
output acrLoginServer string = acr.properties.loginServer
output keyVaultName string = vault.name
output postgresHost string = postgres.properties.fullyQualifiedDomainName
output databases array = [database.name, docqaDatabase.name]
output openAIEndpoint string = openAIEndpoint
output serviceBusNamespace string = bus.name
output apiAppName string = api.name
output webAppName string = web.name
output workerJobName string = worker.name
