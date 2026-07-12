targetScope = 'subscription'

@minLength(1)
@maxLength(64)
@description('Name of the azd environment (used to generate unique resource names).')
param environmentName string

@minLength(1)
@description('Primary Azure region for all resources.')
param location string

// azd injects this after the first image build
param webImageName string = ''

var tags = { 'azd-env-name': environmentName }

resource rg 'Microsoft.Resources/resourceGroups@2022-09-01' = {
  name: 'rg-${environmentName}'
  location: location
  tags: tags
}

module resources 'resources.bicep' = {
  name: 'resources'
  scope: rg
  params: {
    location: location
    tags: tags
    resourceToken: toLower(uniqueString(subscription().id, environmentName, location))
    webImageName: webImageName
  }
}

output AZURE_CONTAINER_REGISTRY_ENDPOINT string = resources.outputs.AZURE_CONTAINER_REGISTRY_ENDPOINT
output AZURE_CONTAINER_REGISTRY_NAME string = resources.outputs.AZURE_CONTAINER_REGISTRY_NAME
output SERVICE_WEB_URI string = resources.outputs.SERVICE_WEB_URI
