@description('Azure region for all resources in this module.')
param location string

@description('Resource tags.')
param tags object

@description('Short unique token derived from subscription + env + location.')
param resourceToken string

@description('Full image reference injected by azd after first build (empty on first provision).')
param webImageName string = ''

// ── Log Analytics ────────────────────────────────────────────────────────────
resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2022-10-01' = {
  name: 'log-${resourceToken}'
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

// ── Container Registry ───────────────────────────────────────────────────────
resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: 'cr${resourceToken}'
  location: location
  tags: tags
  sku: { name: 'Basic' }
  properties: {
    adminUserEnabled: false      // pull via Managed Identity — no admin password
    anonymousPullEnabled: false
  }
}

// ── User-Assigned Managed Identity (for ACR pull) ────────────────────────────
resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-${resourceToken}'
  location: location
  tags: tags
}

// AcrPull built-in role
var acrPullRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '7f951dda-4ed3-4680-a7ca-43fe172d538d'
)

resource acrPullAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: acr
  name: guid(acr.id, identity.id, acrPullRoleId)
  properties: {
    roleDefinitionId: acrPullRoleId
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// ── Container Apps Environment ───────────────────────────────────────────────
resource caEnv 'Microsoft.App/managedEnvironments@2023-05-01' = {
  name: 'cae-${resourceToken}'
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
  }
}

// ── Container App ────────────────────────────────────────────────────────────
// On first provision webImageName is empty, so we use a tiny placeholder image.
// azd replaces the image on first `azd deploy`.
var resolvedImage = empty(webImageName)
  ? 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'
  : webImageName

resource containerApp 'Microsoft.App/containerApps@2023-05-01' = {
  name: 'ca-quranidx-${resourceToken}'
  location: location
  // azd uses the azd-service-name tag to locate the container app for deployment
  tags: union(tags, { 'azd-service-name': 'web' })
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identity.id}': {}
    }
  }
  properties: {
    managedEnvironmentId: caEnv.id
    configuration: {
      ingress: {
        external: true
        targetPort: 5000
        transport: 'auto'
        allowInsecure: false
      }
      registries: [
        {
          server: acr.properties.loginServer
          identity: identity.id
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'web'
          image: resolvedImage
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
          env: [
            { name: 'PORT',        value: '5000' }
            { name: 'OUTPUT_DIR',  value: '/workspace/data/output' }
            { name: 'AUDIO_DIR',   value: '/workspace/data/audio' }
          ]
        }
      ]
      scale: {
        minReplicas: 1   // keep warm — no cold start on demo
        maxReplicas: 3
      }
    }
  }
}

// ── Outputs (consumed by azure.yaml / azd env) ───────────────────────────────
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = acr.properties.loginServer
output AZURE_CONTAINER_REGISTRY_NAME string = acr.name
output SERVICE_WEB_URI string = 'https://${containerApp.properties.configuration.ingress.fqdn}'

// ── Azure AI Services (Speech + Content Understanding) ───────────────────────
// A single multi-service account covers Azure Speech transcription AND
// Azure AI Content Understanding (preview) under one endpoint and key.
resource aiServices 'Microsoft.CognitiveServices/accounts@2023-05-01' = {
  name: 'cog-${resourceToken}'
  location: location
  tags: tags
  kind: 'AIServices'
  sku: { name: 'S0' }
  properties: {
    publicNetworkAccess: 'Enabled'
    customSubDomainName: 'cog-${resourceToken}'
  }
}

output AZURE_AI_ENDPOINT string = aiServices.properties.endpoint
output AZURE_SPEECH_REGION string = location
