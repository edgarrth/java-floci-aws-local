package pe.axiz.platform.configuration;

import com.zaxxer.hikari.HikariConfig;
import com.zaxxer.hikari.HikariDataSource;
import javax.sql.DataSource;
import org.postgresql.ds.PGSimpleDataSource;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.core.env.Environment;
import software.amazon.awssdk.auth.credentials.AwsCredentialsProvider;
import software.amazon.awssdk.regions.Region;
import software.amazon.awssdk.services.rds.RdsUtilities;

@Configuration
@ConditionalOnProperty(name = "axiz.database.enabled", havingValue = "true")
public class DatabaseConfiguration {
  @Bean(destroyMethod = "close")
  public DataSource dataSource(Environment env, AwsCredentialsProvider credentials) {
    String host = env.getRequiredProperty("axiz.database.host");
    int port = env.getRequiredProperty("axiz.database.port", Integer.class);
    String user = env.getRequiredProperty("axiz.database.user");
    var pg = new PGSimpleDataSource();
    pg.setServerNames(new String[] {host});
    pg.setPortNumbers(new int[] {port});
    pg.setDatabaseName(env.getRequiredProperty("axiz.database.name"));
    pg.setSslMode("verify-full");
    pg.setSslRootCert(env.getRequiredProperty("axiz.database.ca"));
    pg.setConnectTimeout(5);
    pg.setSocketTimeout(10);
    var rds =
        RdsUtilities.builder()
            .region(Region.of(env.getRequiredProperty("axiz.aws.region")))
            .credentialsProvider(credentials)
            .build();
    var hc = new HikariConfig();
    hc.setDataSource(new IamDataSource(pg, rds, host, port, user));
    // Se selecciona el esquema después de autenticar; el proxy no transmite opciones de inicio.
    hc.setSchema(env.getRequiredProperty("axiz.database.schema"));
    hc.setMaximumPoolSize(8);
    hc.setMinimumIdle(1);
    hc.setConnectionTimeout(6000);
    hc.setMaxLifetime(600000);
    hc.setPoolName(env.getProperty("spring.application.name") + "-iam");
    return new HikariDataSource(hc);
  }
}
