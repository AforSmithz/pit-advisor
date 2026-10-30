// the agent's model endpoint while bedrock text generation is capped on the aws account.
// bicep rather than cdk because cdk only describes aws

param location string = 'koreacentral'
param env string = 'dev'
param name string = 'pitadvisor-${env}-${uniqueString(resourceGroup().id)}'
param lunaCapacity int = 100
param ossCapacity int = 100

var tags = {
  project: 'pit-advisor'
  component: 'agent-models'
  env: env
}

resource foundry 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: name
  location: location
  tags: tags
  kind: 'AIServices'
  sku: {
    name: 'S0'
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    customSubDomainName: name
    publicNetworkAccess: 'Enabled'
    // gpt-oss-120b only deploys into an account that carries a project
    allowProjectManagement: true
  }
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' = {
  parent: foundry
  name: 'agent'
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {}
}

resource luna 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  parent: foundry
  name: 'gpt-6-luna'
  sku: {
    name: 'GlobalStandard'
    capacity: lunaCapacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: 'gpt-6-luna'
      version: '2026-09-22'
    }
  }
  dependsOn: [project]
}

// deployments on one account have to go one at a time
resource oss 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  parent: foundry
  name: 'gpt-oss-120b'
  sku: {
    name: 'GlobalStandard'
    capacity: ossCapacity
  }
  properties: {
    model: {
      format: 'OpenAI-OSS'
      name: 'gpt-oss-120b'
      version: '1'
    }
  }
  dependsOn: [luna]
}

output endpoint string = 'https://${name}.openai.azure.com/openai/v1'
output account string = foundry.name
