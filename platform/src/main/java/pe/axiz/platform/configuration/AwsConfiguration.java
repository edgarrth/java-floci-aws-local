package pe.axiz.platform.configuration;

import java.net.URI;
import java.nio.file.Path;
import java.time.Duration;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.core.env.Environment;
import software.amazon.awssdk.auth.credentials.*;
import software.amazon.awssdk.awscore.client.builder.AwsClientBuilder;
import software.amazon.awssdk.core.client.config.ClientOverrideConfiguration;
import software.amazon.awssdk.http.urlconnection.UrlConnectionHttpClient;
import software.amazon.awssdk.regions.Region;
import software.amazon.awssdk.services.cloudwatch.CloudWatchClient;
import software.amazon.awssdk.services.dynamodb.DynamoDbClient;
import software.amazon.awssdk.services.kms.KmsClient;
import software.amazon.awssdk.services.s3.S3Client;
import software.amazon.awssdk.services.secretsmanager.SecretsManagerClient;
import software.amazon.awssdk.services.sns.SnsClient;
import software.amazon.awssdk.services.sqs.SqsClient;
import software.amazon.awssdk.services.ssm.SsmClient;
import software.amazon.awssdk.services.sts.StsClient;
import software.amazon.awssdk.services.sts.auth.StsWebIdentityTokenFileCredentialsProvider;

@Configuration
public class AwsConfiguration {
  private final Environment env;

  public AwsConfiguration(Environment env) {
    this.env = env;
  }

  private <B extends AwsClientBuilder<B, ?>> B configure(
      B builder, AwsCredentialsProvider credentials) {
    builder
        .region(Region.of(env.getRequiredProperty("axiz.aws.region")))
        .credentialsProvider(credentials);
    String endpoint = env.getProperty("axiz.aws.endpoint", "");
    if (!endpoint.isBlank()) builder.endpointOverride(URI.create(endpoint));
    return builder;
  }

  @Bean(destroyMethod = "close")
  StsClient sts() {
    var b =
        StsClient.builder()
            .httpClientBuilder(
                UrlConnectionHttpClient.builder()
                    .connectionTimeout(Duration.ofSeconds(2))
                    .socketTimeout(Duration.ofSeconds(3)));
    b.overrideConfiguration(
        ClientOverrideConfiguration.builder().apiCallTimeout(Duration.ofSeconds(5)).build());
    // Se firman las solicitudes de identidad web sin credenciales permanentes.
    return configure(b, AnonymousCredentialsProvider.create()).build();
  }

  @Bean(destroyMethod = "close")
  AwsCredentialsProvider credentials(StsClient sts) {
    String role = env.getProperty("axiz.aws.role-arn", "");
    if (role.isBlank()) return DefaultCredentialsProvider.builder().build();
    return StsWebIdentityTokenFileCredentialsProvider.builder()
        .stsClient(sts)
        .roleArn(role)
        .webIdentityTokenFile(Path.of(env.getRequiredProperty("axiz.aws.token-file")))
        .roleSessionName(env.getProperty("spring.application.name", "axiz"))
        .asyncCredentialUpdateEnabled(true)
        .build();
  }

  @Bean(destroyMethod = "close")
  SqsClient sqs(AwsCredentialsProvider p) {
    return configure(
            SqsClient.builder()
                .httpClientBuilder(
                    UrlConnectionHttpClient.builder()
                        .connectionTimeout(Duration.ofSeconds(2))
                        .socketTimeout(Duration.ofSeconds(5)))
                .overrideConfiguration(
                    ClientOverrideConfiguration.builder()
                        .apiCallTimeout(Duration.ofSeconds(8))
                        .build()),
            p)
        .build();
  }

  @Bean(destroyMethod = "close")
  SnsClient sns(AwsCredentialsProvider p) {
    return configure(
            SnsClient.builder()
                .httpClientBuilder(
                    UrlConnectionHttpClient.builder()
                        .connectionTimeout(Duration.ofSeconds(2))
                        .socketTimeout(Duration.ofSeconds(5)))
                .overrideConfiguration(
                    ClientOverrideConfiguration.builder()
                        .apiCallTimeout(Duration.ofSeconds(8))
                        .build()),
            p)
        .build();
  }

  @Bean(destroyMethod = "close")
  S3Client s3(AwsCredentialsProvider p) {
    return configure(
            S3Client.builder()
                .httpClientBuilder(
                    UrlConnectionHttpClient.builder()
                        .connectionTimeout(Duration.ofSeconds(2))
                        .socketTimeout(Duration.ofSeconds(5)))
                .overrideConfiguration(
                    ClientOverrideConfiguration.builder()
                        .apiCallTimeout(Duration.ofSeconds(8))
                        .build()),
            p)
        .forcePathStyle(true)
        .build();
  }

  @Bean(destroyMethod = "close")
  DynamoDbClient dynamo(AwsCredentialsProvider p) {
    return configure(
            DynamoDbClient.builder()
                .httpClientBuilder(
                    UrlConnectionHttpClient.builder()
                        .connectionTimeout(Duration.ofSeconds(2))
                        .socketTimeout(Duration.ofSeconds(5)))
                .overrideConfiguration(
                    ClientOverrideConfiguration.builder()
                        .apiCallTimeout(Duration.ofSeconds(8))
                        .build()),
            p)
        .build();
  }

  @Bean(destroyMethod = "close")
  SecretsManagerClient secrets(AwsCredentialsProvider p) {
    return configure(
            SecretsManagerClient.builder()
                .httpClientBuilder(
                    UrlConnectionHttpClient.builder()
                        .connectionTimeout(Duration.ofSeconds(2))
                        .socketTimeout(Duration.ofSeconds(5)))
                .overrideConfiguration(
                    ClientOverrideConfiguration.builder()
                        .apiCallTimeout(Duration.ofSeconds(8))
                        .build()),
            p)
        .build();
  }

  @Bean(destroyMethod = "close")
  SsmClient ssm(AwsCredentialsProvider p) {
    return configure(
            SsmClient.builder()
                .httpClientBuilder(
                    UrlConnectionHttpClient.builder()
                        .connectionTimeout(Duration.ofSeconds(2))
                        .socketTimeout(Duration.ofSeconds(5)))
                .overrideConfiguration(
                    ClientOverrideConfiguration.builder()
                        .apiCallTimeout(Duration.ofSeconds(8))
                        .build()),
            p)
        .build();
  }

  @Bean(destroyMethod = "close")
  KmsClient kms(AwsCredentialsProvider p) {
    return configure(
            KmsClient.builder()
                .httpClientBuilder(
                    UrlConnectionHttpClient.builder()
                        .connectionTimeout(Duration.ofSeconds(2))
                        .socketTimeout(Duration.ofSeconds(5)))
                .overrideConfiguration(
                    ClientOverrideConfiguration.builder()
                        .apiCallTimeout(Duration.ofSeconds(8))
                        .build()),
            p)
        .build();
  }

  @Bean(destroyMethod = "close")
  CloudWatchClient cloudWatch(AwsCredentialsProvider p) {
    return configure(
            CloudWatchClient.builder()
                .httpClientBuilder(
                    UrlConnectionHttpClient.builder()
                        .connectionTimeout(Duration.ofSeconds(2))
                        .socketTimeout(Duration.ofSeconds(5)))
                .overrideConfiguration(
                    ClientOverrideConfiguration.builder()
                        .apiCallTimeout(Duration.ofSeconds(8))
                        .build()),
            p)
        .build();
  }
}
