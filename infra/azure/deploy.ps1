<#
.SYNOPSIS
  Deploys LegacyNexus AI to Azure (DEPLOYMENT_PROFILE=cloud): infrastructure, images and app rollout.

.DESCRIPTION
  First run : creates every resource with placeholder images, builds the images in Azure Container
              Registry (no local Docker needed), then rolls them out.
  Later runs: builds new images and redeploys. Existing secrets are reused from Key Vault, never rotated.

.EXAMPLE
  ./deploy.ps1 -ResourceGroup rg-legacynexus-prod -Location westeurope -AdminEmail admin@contoso.com

.EXAMPLE
  # Lowest cost (free-account sizes) with Azure OpenAI gpt-4o-mini + text-embedding-3-small
  ./deploy.ps1 -ResourceGroup rg-legacynexus-dev -Env dev -AdminEmail admin@contoso.com -Sizing free -WithAzureOpenAI
#>
param(
  [Parameter(Mandatory = $true)] [string] $ResourceGroup,
  [string] $Location = "westeurope",
  [string] $NamePrefix = "lnx",
  [string] $Env = "prod",
  [Parameter(Mandatory = $true)] [string] $AdminEmail,
  [switch] $AllowSignup,
  [ValidateSet("free", "standard")] [string] $Sizing = "standard",
  [switch] $WithAzureOpenAI,
  [string] $OpenAILocation = ""
)
$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "../..")
$deploymentName = "legacynexus-$Env"
$tag = (Get-Date -Format "yyyyMMddHHmm")

function New-Secret([int] $bytes = 36) {
  $b = New-Object byte[] $bytes
  [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b)
  return ([Convert]::ToBase64String($b) -replace '[+/=]', 'x')
}

Write-Host "==> Resource group $ResourceGroup ($Location)"
az group create -n $ResourceGroup -l $Location -o none

$me = az ad signed-in-user show --query id -o tsv 2>$null
$principalType = "User"
if (-not $me) { $me = az account show --query user.name -o tsv; $principalType = "ServicePrincipal" }

# Existing deployment? Reuse its Key Vault secrets and registry.
$existing = az deployment group show -g $ResourceGroup -n $deploymentName --query properties.outputs -o json 2>$null | ConvertFrom-Json
$first = -not $existing
$common = @("namePrefix=$NamePrefix", "env=$Env", "bootstrapAdminEmail=$AdminEmail", "allowSignup=$($AllowSignup.IsPresent.ToString().ToLower())",
            "deployerPrincipalId=$me", "deployerPrincipalType=$principalType", "sizing=$Sizing",
            "deployOpenAI=$($WithAzureOpenAI.IsPresent.ToString().ToLower())")
if ($OpenAILocation) { $common += "openAILocation=$OpenAILocation" }

if ($first) {
  Write-Host "==> First deployment: generating secrets (stored only in Key Vault)"
  $dbPassword = "Lnx!" + (New-Secret 24)
  $secretKey = New-Secret 48
  $adminPassword = Read-Host -Prompt "Password for the first administrator ($AdminEmail)" -AsSecureString
  $adminPlain = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($adminPassword))

  Write-Host "==> Creating infrastructure (10-15 minutes)"
  $out = az deployment group create -g $ResourceGroup -n $deploymentName -f (Join-Path $PSScriptRoot "main.bicep") `
    -p $common "dbAdminPassword=$dbPassword" "appSecretKey=$secretKey" "bootstrapAdminPassword=$adminPlain" `
    --query properties.outputs -o json | ConvertFrom-Json
} else {
  $out = $existing
  $dbPassword = az keyvault secret show --vault-name $out.keyVaultName.value -n db-password --query value -o tsv
}

$acr = $out.acrName.value
$server = $out.acrLoginServer.value
Write-Host "==> Building images in $acr (tag $tag)"
az acr build -r $acr -t "legacynexus-api:$tag" (Join-Path $root "backend") -o none
az acr build -r $acr -t "legacynexus-web:$tag" (Join-Path $root "frontend") -o none
$apiImage = "$server/legacynexus-api:$tag"
$webImage = "$server/legacynexus-web:$tag"

Write-Host "==> Rolling out images"
$out = az deployment group create -g $ResourceGroup -n $deploymentName -f (Join-Path $PSScriptRoot "main.bicep") `
  -p $common "dbAdminPassword=$dbPassword" "apiImage=$apiImage" "webImage=$webImage" `
  --query properties.outputs -o json | ConvertFrom-Json

Write-Host ""
Write-Host "LegacyNexus AI is deployed: $($out.webUrl.value)"
Write-Host "Readiness of every backing service: $($out.webUrl.value)/api/health/ready"
